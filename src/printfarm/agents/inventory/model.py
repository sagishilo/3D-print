"""Filament rules (pure): reorder rule, stock assessment and the spool check for a scheduled plate.

Reorder rule (the dashboard's "reorder when grams − rate × (lead + buffer) ≤ min"):

    daily       = max(observed use over the last 14 days / 14, configured baseline)
    projected   = on_hand + on_order − daily × (lead_time + buffer)        what is left when a new order would arrive
    reorder when projected ≤ min_stock
    order       = ceil((min_stock + daily × (lead_time + buffer + review_period) − on_hand − on_order) / spool), ≥ 1
    approval    needed in "manual" mode, or when the order costs more than the auto-approve limit

``buffer`` is the material's safety_stock_days (the design's "+ 2 d"). Money is in agorot (integers).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SPOOL_MARGIN = 1.10      # a scheduled plate needs 10 % more than its estimate on the printer (estimates are estimates)
SOON_DAYS = 3.0          # "reorder soon" when the rule triggers within this many days


@dataclass(frozen=True)
class Policy:
    grams_per_spool: float
    cost_per_spool_agorot: int
    lead_time_days: float
    safety_stock_days: float           # buffer days
    review_period_days: float
    baseline_daily_grams: float
    auto_approve_limit_agorot: int
    min_stock_grams: float = 0.0
    mode: str = "auto"                 # auto | manual


@dataclass(frozen=True)
class Assessment:
    daily_grams: float
    days_left: float | None            # on hand / daily use
    projected_grams: float             # at the moment a new order would arrive
    days_to_trigger: float             # 0 = the rule triggers now
    triggered: bool
    below_min: bool                    # on hand already below the minimum stock
    spools: int                        # what the rule would order now (0 when not triggered)
    est_cost_agorot: int
    needs_approval: bool

    @property
    def state(self) -> str:
        if self.triggered:
            return "triggered"
        return "soon" if self.days_to_trigger <= SOON_DAYS else "ok"


@dataclass(frozen=True)
class ReorderDecision:
    reorder_point: float               # grams at which the rule triggers (without open orders)
    spools: int
    est_cost_agorot: int
    needs_approval: bool


def daily_usage(grams_last_14d: float, baseline: float) -> float:
    return max(grams_last_14d / 14.0, baseline)


def spools_to_order(p: Policy, on_hand: float, on_order_grams: float, daily: float) -> int:
    target = p.min_stock_grams + daily * (p.lead_time_days + p.safety_stock_days + p.review_period_days)
    return max(1, math.ceil((target - on_hand - on_order_grams) / p.grams_per_spool))


def assess(p: Policy, on_hand: float, on_order_grams: float, grams_last_14d: float) -> Assessment:
    daily = daily_usage(grams_last_14d, p.baseline_daily_grams)
    horizon = daily * (p.lead_time_days + p.safety_stock_days)
    projected = on_hand + on_order_grams - horizon
    triggered = projected <= p.min_stock_grams
    days_to_trigger = 0.0 if triggered or daily <= 0 else (projected - p.min_stock_grams) / daily
    spools = spools_to_order(p, on_hand, on_order_grams, daily) if triggered else 0
    cost = spools * int(p.cost_per_spool_agorot)
    return Assessment(
        daily_grams=round(daily, 1), days_left=round(on_hand / daily, 1) if daily > 0 else None,
        projected_grams=round(projected, 1), days_to_trigger=round(days_to_trigger, 1), triggered=triggered,
        below_min=on_hand < p.min_stock_grams, spools=spools, est_cost_agorot=cost,
        needs_approval=triggered and (p.mode == "manual" or cost > p.auto_approve_limit_agorot),
    )


def decide(p: Policy, on_hand: float, on_order_grams: float, grams_last_14d: float) -> ReorderDecision | None:
    """What the inventory agent orders after a consumption, or None while the rule is not triggered."""
    a = assess(p, on_hand, on_order_grams, grams_last_14d)
    if not a.triggered:
        return None
    rop = p.min_stock_grams + a.daily_grams * (p.lead_time_days + p.safety_stock_days)
    return ReorderDecision(round(rop, 1), a.spools, a.est_cost_agorot, a.needs_approval)


@dataclass(frozen=True)
class SpoolCheck:
    needed_grams: float
    available_grams: float

    @property
    def short(self) -> bool:
        return self.available_grams < self.needed_grams


def check_spools(plate_grams: float, loaded_grams: float, reserved_by_other_plates: float) -> SpoolCheck:
    """Does the printer's loaded filament (of the plate's material and color) cover a scheduled plate?"""
    return SpoolCheck(needed_grams=round(plate_grams * SPOOL_MARGIN, 1),
                      available_grams=round(max(loaded_grams - reserved_by_other_plates, 0.0), 1))
