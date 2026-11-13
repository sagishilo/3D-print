"""Ledger rules of the Finance agent (pure functions; the SQL side is covered by tests/test_e2e.py)."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from printfarm.agents.finance.ledger import FinanceSettings, business_day, plate_allocations, round_agorot, split_vat
from printfarm.contracts.messages import JobShare

MURSHE = FinanceSettings()  # VAT 18 %, 64 agorot/kWh, 150 W, 8.5 agorot/g
PATUR = FinanceSettings(business_type="patur")


def test_vat_split_for_osek_murshe():
    assert split_vat(35400, MURSHE) == (30000, 5400)       # 354.00 gross = 300.00 net + 54.00 VAT
    net, vat = split_vat(100, MURSHE)
    assert net + vat == 100 and (net, vat) == (85, 15)


def test_refund_mirrors_the_payment():
    assert split_vat(-35400, MURSHE) == (-30000, -5400)
    for gross in (1, 59, 118, 999, 12345):
        net, vat = split_vat(gross, MURSHE)
        assert split_vat(-gross, MURSHE) == (-net, -vat)


def test_osek_patur_has_no_vat():
    assert split_vat(35400, PATUR) == (35400, 0)


def test_round_half_away_from_zero():
    assert round_agorot(Decimal("2.5")) == 3 and round_agorot(Decimal("-2.5")) == -3


def test_business_day_uses_local_time():
    # 22:30 UTC on Nov 30 is already Dec 1 in Israel (UTC+2): the payment belongs to December
    assert business_day(datetime(2026, 11, 30, 22, 30, tzinfo=UTC), "Asia/Jerusalem") == date(2026, 12, 1)
    assert business_day(datetime(2026, 11, 30, 12, 0, tzinfo=timezone(timedelta(hours=2))), "Asia/Jerusalem") == date(2026, 11, 30)
    assert business_day(None, "Asia/Jerusalem", today=date(2026, 1, 2)) == date(2026, 1, 2)


def shares(*grams):
    return [JobShare(job_id=f"j{i}", order_id="o1", product_type="nameplate", grams=g) for i, g in enumerate(grams, 1)]


def test_plate_cost_is_split_by_grams():
    rows = plate_allocations(grams_used=100, print_seconds=7200, shares=shares(75, 25), material="PLA", color="black",
                             succeeded=True, s=MURSHE)
    filament = {r.job_id: r.amount_agorot for r in rows if r.category == "filament"}
    electricity = {r.job_id: r.amount_agorot for r in rows if r.category == "electricity"}
    assert filament == {"j1": 638, "j2": 213}               # 75 g * 8.5, 25 g * 8.5 (rounded)
    # 2 h * 150 W = 0.3 kWh * 64 = 19.2 agorot, split 75/25
    assert electricity == {"j1": 14, "j2": 5}
    assert all(r.product_type == "nameplate" and r.order_id == "o1" for r in rows)


def test_failed_plate_is_allocated_as_scrap():
    rows = plate_allocations(grams_used=40, print_seconds=1800, shares=shares(40), material="PLA", color="white",
                             succeeded=False, s=MURSHE)
    assert rows and all(r.note.startswith("failed plate (scrap)") for r in rows)


def test_plate_without_shares_becomes_one_row_without_job():
    rows = plate_allocations(grams_used=10, print_seconds=0, shares=[], material="PLA", color="black",
                             succeeded=True, s=MURSHE)
    assert [(r.job_id, r.category, r.amount_agorot) for r in rows] == [(None, "filament", 85)]  # no seconds -> no kWh row


def test_custom_price_and_watts_override_settings():
    rows = plate_allocations(grams_used=10, print_seconds=3600, shares=shares(10), material="PETG", color="clear",
                             succeeded=True, s=MURSHE, printer_watts=Decimal(1000), price_per_gram_agorot=Decimal(10))
    assert {r.category: r.amount_agorot for r in rows} == {"filament": 100, "electricity": 64}
