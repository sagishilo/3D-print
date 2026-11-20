"""Telegram and e-mail alert channels (Wave 2 / W2-D).

Telegram: make a bot with @BotFather, put its token in integrations.env (``TELEGRAM_BOT_TOKEN``), send the bot a
message from each phone that should get alerts, and put those chat ids in the connector's settings (the README says
how to find them). Messages go out through Telegram's Bot API (outbound HTTPS only).

E-mail (SMTP): ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``, ``SMTP_PASSWORD`` in integrations.env (for Gmail: an app
password), the sender and the recipients in the settings. STARTTLS by default.

Both run in fake mode until an owner chooses live: nothing is sent, and the outbox shows what would have gone.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Mapping
from email.message import EmailMessage

import httpx

from printfarm.agents.integrations.channels import COMMON_SETTINGS, AlertChannel
from printfarm.agents.integrations.framework import SecretSpec, SettingSpec, SyncContext, SyncError

TIMEOUT_S = 20


def _list(value: object) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


class Telegram(AlertChannel):
    id = "telegram"
    label = "Telegram"
    secrets = (SecretSpec("TELEGRAM_BOT_TOKEN", "Bot token from @BotFather"),)
    settings = (SettingSpec("chat_ids", "Chat ids that get alerts (comma-separated)", "",
                            pattern=r"-?\d{3,20}(\s*,\s*-?\d{3,20})*"), *COMMON_SETTINGS)

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    def live_missing(self, values: Mapping[str, str], settings: Mapping) -> list[str]:
        return [*super().live_missing(values, settings), *([] if _list(settings.get("chat_ids")) else ["chat ids (settings)"])]

    async def send(self, ctx: SyncContext, message: str) -> None:
        if ctx.mode != "live":
            if ctx.settings.get("fail"):
                raise SyncError("the test channel is set to fail (settings.fail)")
            return
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
        token = ctx.secrets.get("TELEGRAM_BOT_TOKEN", "")
        for chat in _list(ctx.settings.get("chat_ids")):
            try:
                r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                      json={"chat_id": chat, "text": message, "disable_web_page_preview": True})
            except httpx.HTTPError as exc:
                # never the exception's text: it may hold the request URL, and the URL holds the bot token
                raise SyncError(f"Telegram could not be reached ({type(exc).__name__})") from None
            if r.status_code != 200:
                try:
                    why = str(r.json().get("description") or "")[:200]
                except ValueError:
                    why = ""
                raise SyncError(f"Telegram refused the message for chat {chat} (HTTP {r.status_code})"
                                + (f": {why}" if why else ""))


class Email(AlertChannel):
    id = "email"
    label = "E-mail (SMTP)"
    secrets = (SecretSpec("SMTP_HOST", "SMTP server (e.g. smtp.gmail.com)"),
               SecretSpec("SMTP_PORT", "SMTP port (587 with STARTTLS, 465 with SSL)", required=False),
               SecretSpec("SMTP_USER", "SMTP user name", required=False),
               SecretSpec("SMTP_PASSWORD", "SMTP password (Gmail: an app password)", required=False))
    settings = (SettingSpec("from_addr", "Sender address", "", pattern=r"[^@\s]+@[^@\s]+\.[^@\s]+"),
                SettingSpec("to_addrs", "Recipients (comma-separated)", "",
                            pattern=r"[^@\s,]+@[^@\s,]+\.[^@\s,]+(\s*,\s*[^@\s,]+@[^@\s,]+\.[^@\s,]+)*"),
                SettingSpec("security", "starttls, ssl or none", "starttls", pattern=r"starttls|ssl|none"),
                *COMMON_SETTINGS)

    def live_missing(self, values: Mapping[str, str], settings: Mapping) -> list[str]:
        missing = list(super().live_missing(values, settings))
        if not settings.get("from_addr"):
            missing.append("the sender address (settings)")
        if not _list(settings.get("to_addrs")):
            missing.append("recipients (settings)")
        return missing

    @staticmethod
    def build(message: str, *, sender: str, to: list[str]) -> EmailMessage:
        msg = EmailMessage()
        first = message.split("\n", 1)[0]
        msg["Subject"] = f"PrintFarm: {first}"[:180]
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        msg.set_content(message)
        return msg

    async def send(self, ctx: SyncContext, message: str) -> None:
        if ctx.mode != "live":
            if ctx.settings.get("fail"):
                raise SyncError("the test channel is set to fail (settings.fail)")
            return
        s = ctx.settings
        msg = self.build(message, sender=str(s.get("from_addr")), to=_list(s.get("to_addrs")))
        host = ctx.secrets.get("SMTP_HOST", "")
        security = str(s.get("security") or "starttls")
        port = int(ctx.secrets.get("SMTP_PORT") or (465 if security == "ssl" else 587))

        def deliver() -> None:
            context = ssl.create_default_context()
            if security == "ssl":
                server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_S, context=context)
            else:
                server = smtplib.SMTP(host, port, timeout=TIMEOUT_S)
            with server:
                if security == "starttls":
                    server.starttls(context=context)
                if ctx.secrets.get("SMTP_USER"):
                    server.login(ctx.secrets["SMTP_USER"], ctx.secrets.get("SMTP_PASSWORD", ""))
                server.send_message(msg)

        try:
            await asyncio.to_thread(deliver)
        except (OSError, smtplib.SMTPException) as exc:
            raise SyncError(f"the mail server refused it: {type(exc).__name__}: {str(exc)[:200]}") from None
