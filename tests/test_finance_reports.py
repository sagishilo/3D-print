"""Finance reports (pure) and the gateway's finance endpoints (Postgres faked)."""

from __future__ import annotations

from datetime import date

import pytest

from printfarm.agents.finance.reports import (
    ExpenseRow,
    change_pct,
    period_of,
    pnl,
    previous,
    product_margins,
    vat_summary,
)


def test_periods():
    nov = period_of("month", date(2026, 11, 14))
    assert (nov.start, nov.end, nov.label) == (date(2026, 11, 1), date(2026, 12, 1), "Nov 2026")
    assert previous(nov).label == "Oct 2026" and previous(period_of("month", date(2026, 1, 5))).label == "Dec 2025"
    assert period_of("quarter", date(2026, 11, 14)).label == "Q4 2026"
    assert period_of("year", date(2026, 11, 14)).end == date(2027, 1, 1)
    vat = period_of("vat", date(2026, 11, 14))
    assert (vat.start, vat.end, vat.label) == (date(2026, 11, 1), date(2027, 1, 1), "Nov–Dec 2026")
    assert previous(vat).label == "Sep–Oct 2026"
    with pytest.raises(ValueError):
        period_of("week", date(2026, 1, 1))


def test_pnl_counts_purchases_once_and_estimates_electricity_until_the_bill():
    rows = [ExpenseRow("filament", "cash", 50000, 9000),            # spools bought
            ExpenseRow("filament", "allocated", 12000),             # the same filament spread over jobs: not P&L
            ExpenseRow("electricity", "allocated", 6100),           # print hours x watts x tariff
            ExpenseRow("shipping", "cash", 19800)]
    r = pnl(148200, rows)
    assert r.mix == {"filament": 50000, "shipping": 19800, "electricity": 6100}
    assert r.expenses == 75900 and r.profit == 72300 and r.margin_pct == 48.8
    assert r.electricity_estimated and r.electricity_from_jobs
    billed = pnl(148200, [*rows, ExpenseRow("electricity", "cash", 7400, 1129)])
    assert billed.electricity == 7400 and not billed.electricity_estimated and not billed.electricity_from_jobs
    placeholder = pnl(0, [ExpenseRow("electricity", "estimated", 5000)])
    assert placeholder.electricity_estimated and not placeholder.electricity_from_jobs and placeholder.margin_pct is None


def test_product_margins_and_changes():
    m = product_margins({"nameplate": 266000, "other": 5000},
                        [("nameplate", "filament", 18000), ("nameplate", "electricity", 6000), ("riser", "filament", 900)],
                        {"nameplate": 38})
    by = {x.product_type: x for x in m}
    assert by["nameplate"].margin == 242000 and by["nameplate"].margin_pct == 91.0 and by["nameplate"].units == 38
    assert by["riser"].revenue == 0 and by["riser"].margin_pct is None    # cost without revenue still shows
    assert [x.product_type for x in m][0] == "nameplate"
    assert change_pct(110, 100) == 10.0 and change_pct(5, 0) is None and change_pct(-50, -100) == 50.0
    assert vat_summary(5400, 9000).payable == -3600


# ---------------------------------------------------------------- gateway


pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import KEY, build_command, gateway_client, login  # noqa: E402
from pydantic import ValidationError  # noqa: E402


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def test_report_uses_the_pnl_rules(client):
    db = client.app.state.db
    db.rows["sum(vat_agorot), 0) AS vat FROM finance.income_entries"] = [{"net": 100000, "vat": 18000}]
    db.rows["GROUP BY category, kind"] = [{"category": "filament", "kind": "cash", "net": 30000, "vat": 5400},
                                          {"category": "electricity", "kind": "allocated", "net": 2000, "vat": 0}]
    db.values["FROM fleet.plates"] = 31.5
    db.rows["WHERE s.state = 'SHIPPED'"] = [{"order_id": "ord-1", "customer_name": "Noa", "total_agorot": 9000,
                                             "paid_agorot": 4000, "shipped_at": "2026-11-01T10:00:00+00:00"},
                                            {"order_id": "ord-2", "customer_name": "Dan", "total_agorot": 5000,
                                             "paid_agorot": 5000, "shipped_at": "2026-11-02T10:00:00+00:00"}]
    r = client.get("/api/v1/finance/report?month=2026-11").json()
    assert r["period"]["label"] == "Nov 2026" and r["previous"]["label"] == "Oct 2026"
    cur = r["current"]
    assert (cur["revenue_agorot"], cur["expenses_agorot"], cur["profit_agorot"]) == (100000, 32000, 68000)
    assert cur["electricity_estimated"] and cur["electricity_from_jobs"] and r["print_hours"] == 31.5
    assert r["receivables"] == {"total_agorot": 5000, "orders": 1}      # the fully paid order is not owed
    assert client.get("/api/v1/finance/report?month=nov").status_code == 400
    assert client.get("/api/v1/finance/report?period=week").status_code == 422


def test_csv_export_in_shekels_with_a_bom(client):
    db = client.app.state.db
    db.rows["FROM finance.income_entries i"] = [{"entry_date": "2026-11-13", "order_id": "ord-1", "customer_name": "משפחת כהן",
                                                 "method": "bit", "gross_agorot": 35400, "vat_agorot": 5400,
                                                 "net_agorot": 30000, "receipt_no": "RC-1", "product_type": "nameplate",
                                                 "source": "auto"}]
    r = client.get("/api/v1/finance/export.csv?ledger=income&month=2026-11")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="printfarm-income-2026-11-month.csv"' in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("﻿entry_date,order_id,customer_name,method,gross_ils,vat_ils,net_ils")
    assert "משפחת כהן,bit,354.00,54.00,300.00,RC-1" in text
    assert client.get("/api/v1/finance/export.csv?ledger=salaries").status_code == 422


def test_settings_flag_placeholders_until_confirmed(client):
    db = client.app.state.db
    db.rows["FROM finance.settings WHERE id = 1"] = [{"id": 1, "business_type": "murshe", "vat_rate": 0.18,
                                                     "electricity_agorot_per_kwh": 71, "default_printer_watts": 150,
                                                     "default_filament_agorot_per_gram": 8.5, "confirmed_at": None}]
    s = client.get("/api/v1/finance/settings").json()
    assert s["placeholders"] == ["vat_rate", "default_printer_watts", "default_filament_agorot_per_gram"]
    assert not s["confirmed"]
    env = build_command("finance.settings", {"business_type": "murshe", "vat_rate": 0.18, "electricity_agorot_per_kwh": 71,
                                             "default_printer_watts": 120, "default_filament_agorot_per_gram": 9,
                                             "confirm": True}, "admin", KEY)
    assert env.payload().updated_by == "admin" and env.nats_subject() == "cmd.finance.update_settings"
    with pytest.raises(ValidationError):
        build_command("finance.settings", {"business_type": "company", "vat_rate": 0.18, "electricity_agorot_per_kwh": 71,
                                           "default_printer_watts": 120, "default_filament_agorot_per_gram": 9}, "admin", KEY)


def test_finance_needs_a_session():
    with gateway_client() as anonymous:
        for path in ("/api/v1/finance/report", "/api/v1/finance/vat", "/api/v1/finance/export.csv?ledger=income"):
            assert anonymous.get(path).status_code == 401, path
