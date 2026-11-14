"""Filament rules (pure): minimum stock, reorder mode, the reorder list's states and the spool check; plus a contract
guard that no message field hides an envelope attribute."""

from __future__ import annotations

import pytest

from printfarm.agents.inventory.model import Policy, assess, check_spools, decide
from printfarm.contracts import REGISTRY
from printfarm.contracts.envelope import Payload

RULE = Policy(grams_per_spool=1000, cost_per_spool_agorot=8500, lead_time_days=4, safety_stock_days=2,
              review_period_days=14, baseline_daily_grams=100, auto_approve_limit_agorot=20000, min_stock_grams=1000)


def test_the_rule_keeps_stock_above_the_minimum():
    # projected = on hand - 100 g/d x (4 + 2) d; triggers at 1000 g minimum -> at 1600 g on hand
    assert decide(RULE, on_hand=1700, on_order_grams=0, grams_last_14d=0) is None
    d = decide(RULE, on_hand=1600, on_order_grams=0, grams_last_14d=0)
    assert d.reorder_point == 1600
    # order up to min + 100 x (4 + 2 + 14) = 3000 g -> 2 spools, 170.00 ILS: under the limit, no approval
    assert (d.spools, d.est_cost_agorot, d.needs_approval) == (2, 17000, False)
    assert decide(RULE, on_hand=1600, on_order_grams=1000, grams_last_14d=0) is None   # open order counts


def test_manual_mode_always_asks_and_the_limit_still_applies():
    manual = Policy(**{**RULE.__dict__, "mode": "manual"})
    assert decide(manual, on_hand=1500, on_order_grams=0, grams_last_14d=0).needs_approval
    expensive = decide(RULE, on_hand=0, on_order_grams=0, grams_last_14d=14 * 300)
    assert expensive.spools == 7 and expensive.needs_approval   # up to 1000 + 300 x 20 g: 595.00 ILS > 200.00 ILS


@pytest.mark.parametrize(("on_hand", "state", "days_to_trigger", "below_min"), [
    (5000, "ok", 34.0, False),
    (1800, "soon", 2.0, False),
    (1200, "triggered", 0.0, False),
    (600, "triggered", 0.0, True),
])
def test_reorder_list_states(on_hand, state, days_to_trigger, below_min):
    a = assess(RULE, on_hand, 0, 0)
    assert (a.state, a.days_to_trigger, a.below_min) == (state, days_to_trigger, below_min)
    assert a.days_left == on_hand / 100 and a.daily_grams == 100
    assert (a.spools > 0) == (state == "triggered")


def test_spool_check_for_a_scheduled_plate():
    ok = check_spools(plate_grams=100, loaded_grams=400, reserved_by_other_plates=200)
    assert not ok.short and (ok.needed_grams, ok.available_grams) == (110.0, 200.0)
    short = check_spools(plate_grams=100, loaded_grams=300, reserved_by_other_plates=200)
    assert short.short and short.available_grams == 100.0     # 10 % margin: 110 g needed
    assert check_spools(50, 0, 0).short                       # nothing loaded


def test_no_message_field_hides_an_envelope_or_payload_attribute():
    # A field named like a Payload class attribute ("kind", "subject", "version") silently shadows it.
    reserved = {"kind", "subject", "version"} | {n for n in dir(Payload) if not n.startswith("_")}
    for model in REGISTRY.values():
        clash = reserved & set(model.model_fields)
        assert not clash, f"{model.__name__} has field(s) {clash}"
