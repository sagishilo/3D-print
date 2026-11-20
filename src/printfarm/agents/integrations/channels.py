"""Alert channels (Wave 2 / W2-D): the notifications outbox delivered to people: Telegram and e-mail (SMTP).

Pure rules here: ``route`` decides, per channel, whether a notification goes now, waits for the end of quiet hours,
or is not for this channel (below its minimum severity). A critical (safety) notification always goes, quiet hours
or not. ``text`` writes the message (Hebrew or English, with a link to the dashboard when its address is set).

Fake mode (the default) sends nothing: the agent records what would have gone, so the dashboard and the tests show
the whole flow. Live mode needs the channel's secrets in integrations.env.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from printfarm.agents.integrations.framework import Connector, SettingSpec, SyncContext, SyncResult

SEVERITY = {"info": 0, "warning": 1, "critical": 2}
SEVERITY_PATTERN = r"info|warning|critical"
HOUR_PATTERN = r"([01]\d|2[0-3]):[0-5]\d"


@dataclass(frozen=True)
class OutNote:
    id: int
    topic: str
    severity: str
    title: str
    body: str = ""
    link: str = ""


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_quiet(now: time, start: str, end: str) -> bool:
    """Is ``now`` within quiet hours start..end (which may cross midnight, e.g. 22:00-07:00)?"""
    a, b = _hm(start), _hm(end)
    if a == b:
        return False
    return a <= now < b if a < b else now >= a or now < b


def quiet_ends(local_now: datetime, start: str, end: str) -> datetime:
    """When the current quiet period ends (local time)."""
    e = _hm(end)
    out = local_now.replace(hour=e.hour, minute=e.minute, second=0, microsecond=0)
    return out if out > local_now else out + timedelta(days=1)


def route(note: OutNote, settings: Mapping, local_now: datetime) -> str:
    """send | hold (quiet hours) | skip (not for this channel)."""
    if note.topic == "test":
        return "send"
    if SEVERITY.get(note.severity, 0) < SEVERITY.get(str(settings.get("min_severity") or "warning"), 1):
        return "skip"
    if note.severity == "critical":
        return "send"                    # safety beats quiet hours
    if settings.get("quiet_enabled", True) and in_quiet(local_now.time(), str(settings.get("quiet_from") or "22:00"),
                                                        str(settings.get("quiet_until") or "07:00")):
        return "hold"
    return "send"


# ---------------------------------------------------------------- the message, in Hebrew or English

_HE: dict[str, str] | None = None
_PH = re.compile(r"\{(\w+)\}")


def _hebrew() -> dict[str, str]:
    """The Hebrew of the server's notification text (generated from the dashboard's dictionary)."""
    global _HE
    if _HE is None:
        try:
            _HE = json.loads((pathlib.Path(__file__).with_name("he_notify.json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _HE = {}
    return _HE


_RULES: list[tuple[re.Pattern, list[str], str]] | None = None
_WEIGHTS: dict[re.Pattern, int] = {}


def _weight(rx: re.Pattern) -> int:
    """Letters a pattern has of its own: a short one ("{0} of {1}") takes only single words as values."""
    return _WEIGHTS.get(rx, 0)


def _rules() -> list[tuple[re.Pattern, list[str], str]]:
    global _RULES
    if _RULES is None:
        out = []
        for en, he in _hebrew().items():
            names, parts, last = [], [], 0
            for m in _PH.finditer(en):
                parts += [re.escape(en[last:m.start()]), "(.*?)"]
                names.append(m.group(1))
                last = m.end()
            weight = len(re.sub(r"[^A-Za-z]", "", _PH.sub("", en)))
            if names and weight >= 2:
                out.append((re.compile("^" + "".join(parts) + re.escape(en[last:]) + "$", re.S), names, he, weight))
        _RULES = [(rx, names, he) for rx, names, he, _ in sorted(out, key=lambda r: -r[3])]
        _WEIGHTS.update({rx: w for rx, _, _, w in out})
    return _RULES


def to_hebrew(text: str) -> str:
    """Exact text or a pattern with values ("Order {0} needs attention"), else the English as it is."""
    core = re.sub(r"\s+", " ", text).strip()
    he = _hebrew()
    if core in he:
        return he[core]
    for rx, names, out in _rules():
        m = rx.match(core)
        if m and (_weight(rx) >= 12 or not any(re.search(r"\s", v.strip()) and re.search(r"[A-Za-z]{3}", v)
                                                for v in m.groups())):
            values = dict(zip(names, m.groups(), strict=True))
            return _PH.sub(lambda g, v=values: v.get(g.group(1), ""), out)
    return text


SEVERITY_ICON = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}


def text(note: OutNote, *, lang: str, dashboard_url: str) -> str:
    title, body = (to_hebrew(note.title), to_hebrew(note.body)) if lang == "he" else (note.title, note.body)
    lines = [f"{SEVERITY_ICON.get(note.severity, '')} {title}".strip()]
    if body:
        lines.append(body)
    if note.link and dashboard_url:
        lines.append(dashboard_url.rstrip("/") + note.link)
    return "\n".join(lines)[:3500]


# ---------------------------------------------------------------- the channel connector

COMMON_SETTINGS = (
    SettingSpec("min_severity", "Send from this severity up (info, warning, critical)", "warning", pattern=SEVERITY_PATTERN),
    SettingSpec("quiet_enabled", "Quiet hours (only critical safety alerts go out)", True),
    SettingSpec("quiet_from", "Quiet from (HH:MM)", "22:00", pattern=HOUR_PATTERN),
    SettingSpec("quiet_until", "Quiet until (HH:MM)", "07:00", pattern=HOUR_PATTERN),
    SettingSpec("lang", "Message language (he or en)", "he", pattern=r"he|en"),
    SettingSpec("dashboard_url", "Dashboard address for links (e.g. https://printfarm.local)", "",
                pattern=r"https://[A-Za-z0-9.:\-]+(/[A-Za-z0-9._\-/]*)?"),
)


class AlertChannel(Connector):
    kind = "alerts"
    default_interval_s = 3600

    async def send(self, ctx: SyncContext, message: str) -> None:        # pragma: no cover - per channel
        raise NotImplementedError

    async def check(self, ctx: SyncContext) -> None:
        return None

    async def sync(self, ctx: SyncContext) -> SyncResult:
        await self.check(ctx)
        return SyncResult([], dict(ctx.cursor))
