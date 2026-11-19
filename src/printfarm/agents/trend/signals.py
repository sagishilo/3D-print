"""Trend rules (pure): what is rising or falling, per niche and per SKU, from the daily analytics.

A subject is *rising* when it sold at least MIN_UNITS in the last 7 days and that is RISE_PCT more than the 7 days
before (or it sold that much from nothing), or when social intent jumps (leads at least doubled, MIN_LEADS or more).
It is *falling* when it sold at least MIN_UNITS the week before and RISE_PCT less now. The 28-day comparison is
evidence, not a trigger: one good week of a slow product shows, a seasonal slide shows over the month.
"""

from __future__ import annotations

from dataclasses import dataclass

from printfarm.contracts.catalog import trend_pct

MIN_UNITS = 3
RISE_PCT = 30.0
MIN_LEADS = 2


@dataclass(frozen=True)
class Window:
    scope: str                 # "niche" | "sku"
    subject: str               # niche slug or SKU
    niche: str
    units_7d: int = 0
    units_prev_7d: int = 0
    units_28d: int = 0
    units_prev_28d: int = 0
    leads_7d: int = 0
    leads_prev_7d: int = 0
    revenue_7d: int = 0


@dataclass(frozen=True)
class Signal:
    scope: str
    subject: str
    niche: str
    direction: str             # "rising" | "falling"
    strength: float            # 0..1
    change_pct: float | None   # units, 7 days against the 7 before (None: from nothing)
    evidence: tuple[str, ...]

    @property
    def topic(self) -> str:
        return f"{self.scope}:{self.subject}"


def _evidence(w: Window) -> list[str]:
    out = [f"{w.units_7d} sold in the last 7 days ({w.units_prev_7d} the 7 days before)"]
    if w.units_28d or w.units_prev_28d:
        out.append(f"28 days: {w.units_28d} sold ({w.units_prev_28d} the 28 days before)")
    if w.leads_7d or w.leads_prev_7d:
        out.append(f"{w.leads_7d} social lead{'s' if w.leads_7d != 1 else ''} this week ({w.leads_prev_7d} the week before)")
    return out


def signal_of(w: Window) -> Signal | None:
    change = trend_pct(w.units_7d, w.units_prev_7d)
    ev = tuple(_evidence(w))
    if w.units_7d >= MIN_UNITS and (change is None or change >= RISE_PCT):
        strength = 1.0 if change is None else min(1.0, change / 100)
        return Signal(w.scope, w.subject, w.niche, "rising", round(strength, 2), change, ev)
    if w.units_prev_7d >= MIN_UNITS and change is not None and change <= -RISE_PCT:
        return Signal(w.scope, w.subject, w.niche, "falling", round(min(1.0, -change / 100), 2), change, ev)
    if w.leads_7d >= MIN_LEADS and w.leads_7d >= 2 * w.leads_prev_7d:
        ratio = w.leads_7d / max(w.leads_prev_7d, 1)
        return Signal(w.scope, w.subject, w.niche, "rising", round(min(1.0, ratio / 4), 2), change,
                      (f"social intent: {w.leads_7d} leads this week ({w.leads_prev_7d} the week before)", *ev))
    return None


def detect(windows: list[Window]) -> list[Signal]:
    """Every rising or falling subject, strongest first."""
    out = [s for w in windows if (s := signal_of(w)) is not None]
    return sorted(out, key=lambda s: (-s.strength, s.topic))


def fake_insight(name: str, w: Window, rising: list[str]) -> str:
    """The insight without a model (tests, demos): the numbers in one Hebrew sentence."""
    change = trend_pct(w.units_7d, w.units_prev_7d)
    trend = "ללא שינוי" if change is None or abs(change) < RISE_PCT else ("עלייה" if change > 0 else "ירידה")
    top = f" מובילים: {', '.join(rising[:3])}." if rising else ""
    return f"{name}: {w.units_7d} יחידות נמכרו השבוע ({trend} מול השבוע הקודם), {w.leads_7d} פניות ברשתות.{top}"
