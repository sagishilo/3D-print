import pytest

from printfarm.agents.inventory.model import Policy, decide
from printfarm.agents.personalization.nameplate import (
    InvalidText,
    NameplateSpec,
    estimate,
    generate,
    is_rtl,
    render_scad,
    scad_string,
)

POLICY = Policy(grams_per_spool=1000, cost_per_spool_agorot=2200, lead_time_days=5, safety_stock_days=3,
                review_period_days=14, baseline_daily_grams=150, auto_approve_limit_agorot=5000)


def test_no_reorder_above_rop():
    assert decide(POLICY, on_hand=5000, on_order_grams=0, grams_last_14d=0) is None


def test_reorder_below_rop_requires_approval_over_limit():
    d = decide(POLICY, on_hand=1000, on_order_grams=0, grams_last_14d=0)
    assert d.reorder_point == 1200
    assert d.spools == 3 and d.est_cost_agorot == 6600 and d.needs_approval
    assert isinstance(d.est_cost_agorot, int)


def test_open_orders_count_towards_position():
    assert decide(POLICY, on_hand=1000, on_order_grams=1000, grams_last_14d=0) is None


def test_observed_usage_beats_baseline():
    d = decide(POLICY, on_hand=1000, on_order_grams=0, grams_last_14d=14 * 400)
    assert d.reorder_point == 3200


def test_hebrew_is_rtl_and_rendered():
    assert is_rtl("נועה") and not is_rtl("Noa")
    scad = render_scad(NameplateSpec(text="משפחת כהן"))
    assert 'direction = "rtl"' in scad and "משפחת כהן" in scad


@pytest.mark.parametrize("bad", ["", "x" * 41, "a;b", "evil}import(\"/etc\")", "<script>"])
def test_text_validation(bad):
    with pytest.raises(InvalidText):
        render_scad(NameplateSpec(text=bad))


def test_quotes_are_escaped():
    assert scad_string('say "hi" \\') == '"say \\"hi\\" \\\\"'
    scad = render_scad(NameplateSpec(text='Bar "Moshe"'))
    assert 'text("Bar \\"Moshe\\""' in scad


def test_estimates_are_positive():
    g, m = estimate(NameplateSpec(text="x"))
    assert 5 < g < 50 and m > 5


async def test_generate_without_tools(tmp_path):
    res = await generate(NameplateSpec(text="Noa"), tmp_path, "ord-1")
    assert res.source_path.exists() and res.footprint_mm == (120.0, 35.0)
