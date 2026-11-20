"""W2-D alert channels, pure parts: quiet hours (across midnight), the route of a notification per channel (minimum
severity, quiet hours held, critical safety always sent, a test always sent), the message in Hebrew or English with
a dashboard link, the server text's Hebrew, Telegram's request (no network) and errors that never show the bot
token, the e-mail, what live mode needs, and the gateway's owner-only test command."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import httpx
import pytest

from printfarm.agents.integrations import channels
from printfarm.agents.integrations.channels import OutNote, in_quiet, quiet_ends, route, to_hebrew
from printfarm.agents.integrations.framework import SyncContext, SyncError, clean_settings
from printfarm.agents.integrations.telegram import Email, Telegram

IL = ZoneInfo("Asia/Jerusalem")
NIGHT = datetime(2026, 11, 20, 23, 30, tzinfo=IL)
DAY = datetime(2026, 11, 20, 14, 0, tzinfo=IL)
S = {"min_severity": "warning", "quiet_enabled": True, "quiet_from": "22:00", "quiet_until": "07:00"}


def note(severity="warning", topic="maintenance", **kw):
    return OutNote(1, topic, severity, kw.pop("title", "Maintenance overdue on p1: Lube"), kw.pop("body", ""),
                   kw.pop("link", "/fleet?printer=p1"))


def test_quiet_hours_cross_midnight():
    assert in_quiet(time(23, 0), "22:00", "07:00") and in_quiet(time(6, 59), "22:00", "07:00")
    assert not in_quiet(time(7, 0), "22:00", "07:00") and not in_quiet(time(14, 0), "22:00", "07:00")
    assert in_quiet(time(13, 0), "12:00", "14:00") and not in_quiet(time(14, 0), "12:00", "14:00")
    assert not in_quiet(time(3, 0), "05:00", "05:00")
    assert quiet_ends(NIGHT, "22:00", "07:00") == datetime(2026, 11, 21, 7, 0, tzinfo=IL)
    assert quiet_ends(datetime(2026, 11, 21, 5, 0, tzinfo=IL), "22:00", "07:00") == datetime(2026, 11, 21, 7, 0, tzinfo=IL)


def test_the_route_of_a_notification():
    assert route(note(), S, DAY) == "send"
    assert route(note(), S, NIGHT) == "hold"
    assert route(note("critical", "safety"), S, NIGHT) == "send"          # safety beats quiet hours
    assert route(note("info"), S, DAY) == "skip"
    assert route(note("info"), {**S, "min_severity": "info"}, DAY) == "send"
    assert route(note(), {**S, "quiet_enabled": False}, NIGHT) == "send"
    assert route(note("info", "test"), {**S, "min_severity": "critical"}, NIGHT) == "send"
    assert route(note("warning"), {**S, "min_severity": "critical"}, DAY) == "skip"


def test_the_message_in_hebrew_or_english():
    n = note(title="Maintenance overdue on p1: Lube", body="The printer gets no new plates until it is done.")
    he = channels.text(n, lang="he", dashboard_url="https://printfarm.local/")
    assert he == ("⚠️ תחזוקה שעבר זמנה ב-p1: Lube\nהמדפסת לא מקבלת פלטות חדשות עד שזה נעשה.\n"
                  "https://printfarm.local/fleet?printer=p1")
    en = channels.text(n, lang="en", dashboard_url="")
    assert en == "⚠️ Maintenance overdue on p1: Lube\nThe printer gets no new plates until it is done."
    assert to_hebrew("Something nobody wrote") == "Something nobody wrote"
    assert to_hebrew("3 store orders wait for you") == "3 הזמנות מהחנות ממתינות לכם"


def ctx(mode="fake", settings=None, secrets=None):
    return SyncContext(mode=mode, cursor={}, settings=settings or {}, secrets=secrets or {},
                       now=datetime(2026, 11, 20, tzinfo=UTC), log=lambda m: None)


class FakeHttp:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def post(self, url, **kw):
        self.calls.append((url, kw))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return httpx.Response(a[0], json=a[1], request=httpx.Request("POST", url))


def test_telegram_sends_to_every_chat_and_never_shows_its_token():
    http = FakeHttp((200, {"ok": True}), (200, {"ok": True}))
    t = Telegram(client=http)
    asyncio.run(t.send(ctx("live", {"chat_ids": "123456, -100987654"}, {"TELEGRAM_BOT_TOKEN": "777:SECRET"}), "hi"))
    assert [c[1]["json"]["chat_id"] for c in http.calls] == ["123456", "-100987654"]
    assert http.calls[0][0] == "https://api.telegram.org/bot777:SECRET/sendMessage"
    for answer, msg in (((403, {"description": "Forbidden: bot was blocked by the user"}), "blocked by the user"),
                        (httpx.ConnectError("https://api.telegram.org/bot777:SECRET/sendMessage"), "could not be reached")):
        with pytest.raises(SyncError, match=msg) as exc:
            asyncio.run(Telegram(client=FakeHttp(answer)).send(
                ctx("live", {"chat_ids": "1234"}, {"TELEGRAM_BOT_TOKEN": "777:SECRET"}), "hi"))
        assert "SECRET" not in str(exc.value)
    asyncio.run(Telegram().send(ctx(), "hi"))                                   # test mode: nothing sent
    with pytest.raises(SyncError, match="set to fail"):
        asyncio.run(Telegram().send(ctx(settings={"fail": True}), "hi"))
    assert Telegram().live_missing({}, {}) == ["TELEGRAM_BOT_TOKEN", "chat ids (settings)"]
    with pytest.raises(ValueError):
        clean_settings(Telegram().settings, {"chat_ids": "not a chat"})
    with pytest.raises(ValueError):
        clean_settings(Telegram().settings, {"dashboard_url": "http://insecure.local"})


def test_email_message_and_what_live_needs():
    m = Email.build("⚠️ Maintenance overdue\nDo it today.", sender="farm@example.com", to=["a@example.com", "b@example.com"])
    assert m["Subject"] == "PrintFarm: ⚠️ Maintenance overdue" and m["To"] == "a@example.com, b@example.com"
    assert "Do it today." in m.get_content()
    assert Email().live_missing({"SMTP_HOST": "smtp.example.com"}, {}) == ["the sender address (settings)", "recipients (settings)"]
    assert Email().live_missing({}, {"from_addr": "a@b.co", "to_addrs": "c@d.co"}) == ["SMTP_HOST"]
    asyncio.run(Email().send(ctx(), "hi"))


def test_the_gateway_test_command_is_for_owners():
    import gateway_support  # noqa: F401 - sets the gateway's environment

    from gateway.app import commands
    assert commands.COMMANDS["integrations.test_alert"].min_role == "owner"
