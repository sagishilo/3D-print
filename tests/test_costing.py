"""Wave 1 / M5: unit economics (pure, worked examples for an osek murshe and an osek patur), the recommended price,
the quote text, and the gateway's unit-economics and quote endpoints (Postgres faked)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from printfarm.agents.finance.costing import (
    CostInputs,
    failure_rate,
    fixed_costs,
    nice_price,
    quote_text,
    recommended_price,
    unit_economics,
)
from printfarm.agents.finance.ledger import FinanceSettings

MURSHE = FinanceSettings()                                             # VAT 18%, 64 agorot / kWh
PATUR = FinanceSettings(business_type="patur")

# A 50 g, 2-hour piece with 10 minutes of finishing, 3 shekels of parts, a 10% failure rate.
PIECE = CostInputs(grams=50, print_minutes=120, agorot_per_gram=Decimal("8.5"), printer_watts=Decimal(150),
                   labour_minutes=10, labour_agorot_per_hour=Decimal(4000), parts_agorot=300, failure_rate=0.1)


def test_the_cost_of_one_piece_part_by_part():
    c = fixed_costs(PIECE, MURSHE)
    # filament 50 x 8.5; power 2 h x 150 W = 0.3 kWh x 64; failures (425 + 19.2) x 0.1 / 0.9; labour 10/60 x 4000
    assert c == {"filament": 425, "electricity": 19, "failures": 49, "labour": 667, "parts": 300, "shipping": 0}


def test_margin_and_profit_per_print_hour_at_a_price():
    e = unit_economics(PIECE, MURSHE, gross_price=3900, target_margin_pct=50)
    assert (e.net_price, e.unit_cost, e.margin) == (3305, 1460, 1845)           # 39 shekels incl. VAT = 33.05 net
    assert e.margin_pct == 55.8 and e.profit_per_print_hour == 922 and not e.below_target
    assert e.recommended_price == 3900                                          # 34.46 needed -> ends in 9


def test_vat_makes_a_murshe_price_higher_than_a_patur_one():
    assert recommended_price(1460, 60, 0, MURSHE) == 4900                      # net 36.50 x 1.18 = 43.07 -> 49
    assert recommended_price(1460, 60, 0, PATUR) == 3900                       # no VAT: 36.50 -> 39
    patur = unit_economics(PIECE, PATUR, gross_price=3900)
    assert patur.net_price == 3900 and patur.margin == 2440


def test_a_channel_fee_is_a_cost_on_the_gross_price():
    etsy = CostInputs(**{**PIECE.__dict__, "channel_fee_pct": 10})
    e = unit_economics(etsy, MURSHE, gross_price=4900, target_margin_pct=50)
    assert e.costs["channel_fee"] == 490 and e.margin == 4153 - 1460 - 490
    assert e.recommended_price == 4900                                          # 45.10 needed after the fee
    assert recommended_price(1460, 60, 40, MURSHE) is None                     # nothing is left for a margin


def test_below_target_and_free_items():
    e = unit_economics(PIECE, MURSHE, gross_price=2000, target_margin_pct=50)
    assert e.below_target and e.margin_pct == round((1695 - 1460) * 100 / 1695, 1)
    assert unit_economics(PIECE, MURSHE, gross_price=0).margin_pct is None


@pytest.mark.parametrize("agorot,nice", [(1234, 1290), (1290, 1290), (50, 90), (2000, 2900), (3446, 3900), (4900, 4900),
                                         (5010, 5900), (0, 0)])
def test_prices_end_in_9(agorot, nice):
    assert nice_price(agorot) == nice


def test_failure_rate_counts_plates_and_qc_and_is_capped():
    assert failure_rate(1, 10, 2, 20) == pytest.approx(0.19)
    assert failure_rate(0, 0, 0, 0) == 0
    assert failure_rate(9, 10, 0, 0) == 0.5


def test_the_quote_text_for_whatsapp():
    text = quote_text(title="שלט שם לדלת", quantity=2, unit_price=4900, extras=1000, shipping=2500, days=4)
    assert "2 × ₪49 = ₪98" in text and "משלוח: ₪25" in text and "סה\"כ לתשלום: ₪133" in text
    assert "4 ימי עבודה" in text and "כולל מע\"מ" in text
    assert "כולל" not in quote_text(title="x", quantity=1, unit_price=1000, vat_included=False)


# ---------------------------------------------------------------- gateway (Postgres faked)

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import csrf, gateway_client, login  # noqa: E402

SETTINGS_ROW = {"business_type": "murshe", "vat_rate": Decimal("0.18"), "electricity_agorot_per_kwh": Decimal(64),
                "default_printer_watts": Decimal(150), "default_filament_agorot_per_gram": Decimal("8.5"),
                "labour_agorot_per_hour": Decimal(4000), "channel_fees": {"etsy": 10}}


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        db = c.app.state.db
        db.rows["SELECT * FROM finance.settings"] = [SETTINGS_ROW]
        db.rows["FROM fleet.plates"] = [{"failed": 1, "total": 10}]
        db.rows["FROM coordinator.qc_checks"] = [{"failed": 0, "checked": 0}]
        db.rows["FROM inventory.bom b"] = [{"sku": "HOOK", "agorot": 300}]
        db.rows["FROM catalog.niches"] = [{"slug": "home", "name": "Home décor", "color": 1, "target_margin_pct": 50.0}]
        db.rows["FROM catalog.products WHERE active"] = [
            {"sku": "HOOK", "name": "Hook", "niche": "home", "material": "PLA", "color": "black", "price_agorot": 2000,
             "est_grams": 50.0, "est_minutes": 120.0, "estimated": False, "post_minutes": 10.0}]
        db.rows["FROM catalog.products WHERE sku"] = db.rows["FROM catalog.products WHERE active"]
        yield c


def test_unit_economics_flags_a_product_below_its_niche_target(client):
    r = client.get("/api/v1/finance/unit-economics").json()
    hook = r["products"][0]
    assert hook["costs"]["parts"] == 300 and hook["costs"]["labour"] == 667 and hook["below_target"] is True
    assert hook["recommended_price"] == 3900 and hook["target_margin_pct"] == 50.0 and r["below_target"] == 1
    assert r["failure_rate"] == 0.1
    etsy = client.get("/api/v1/finance/unit-economics?channel=etsy").json()
    assert etsy["channel_fee_pct"] == 10 and etsy["products"][0]["costs"]["channel_fee"] == 200
    assert client.get("/api/v1/finance/unit-economics?channel=moon").status_code == 400


def test_a_quote_for_a_sku_and_for_a_custom_print(client):
    h = {**csrf(client)}
    r = client.post("/api/v1/pricing/quote", headers=h, json={"sku": "HOOK", "quantity": 3, "shipping_agorot": 2500}).json()
    assert r["unit_price"] == 3900 and r["total_agorot"] == 3 * 3900 + 2500 and r["title"] == "Hook"
    assert "3 × ₪39" in r["text"]
    custom = client.post("/api/v1/pricing/quote", headers=h, json={"grams": 200, "print_minutes": 600, "target_margin_pct": 40,
                                                                    "title": "Cosplay helmet"}).json()
    assert custom["unit_price"] == custom["recommended_price"] and custom["margin_pct"] >= 40
    check = client.post("/api/v1/pricing/quote", headers=h, json={"sku": "HOOK", "unit_price_agorot": 2000}).json()
    assert check["unit_price"] == 2000 and check["below_target"] is True
    assert client.post("/api/v1/pricing/quote", headers=h, json={"quantity": 1}).status_code == 422


def test_pricing_is_not_for_viewers():
    with gateway_client() as anonymous:
        assert anonymous.get("/api/v1/finance/unit-economics").status_code == 401
