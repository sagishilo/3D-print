"""Reorder policy (pure): continuous review, reorder point + order-up-to level.

    daily      = max(observed average over the last 14 days, configured baseline)
    ROP        = daily * (lead_time + safety_stock_days)
    order-up-to= daily * (lead_time + safety_stock_days + review_period)
    reorder when on_hand + on_order < ROP, for ceil((order-up-to - on_hand - on_order) / spool) spools

Money is in agorot (integers).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    grams_per_spool: float
    cost_per_spool_agorot: int
    lead_time_days: float
    safety_stock_days: float
    review_period_days: float
    baseline_daily_grams: float
    auto_approve_limit_agorot: int


@dataclass(frozen=True)
class ReorderDecision:
    reorder_point: float
    spools: int
    est_cost_agorot: int
    needs_approval: bool


def daily_usage(grams_last_14d: float, baseline: float) -> float:
    return max(grams_last_14d / 14.0, baseline)


def decide(p: Policy, on_hand: float, on_order_grams: float, grams_last_14d: float) -> ReorderDecision | None:
    daily = daily_usage(grams_last_14d, p.baseline_daily_grams)
    rop = daily * (p.lead_time_days + p.safety_stock_days)
    if on_hand + on_order_grams >= rop:
        return None
    up_to = daily * (p.lead_time_days + p.safety_stock_days + p.review_period_days)
    spools = max(1, math.ceil((up_to - on_hand - on_order_grams) / p.grams_per_spool))
    cost = spools * int(p.cost_per_spool_agorot)
    return ReorderDecision(round(rop, 1), spools, cost, needs_approval=cost > p.auto_approve_limit_agorot)
