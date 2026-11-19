"""Wave 1 / M6: the Trend Analyst's rules (pure), the fake insight model, and the gateway's scorecard, signals and
downloads (CSV with a BOM, Excel opened with openpyxl). Postgres faked; tests/test_trend_pg.py runs the roll-up."""

from __future__ import annotations

import importlib
import io
from datetime import date

import pytest

from printfarm.agents.trend.insight import FakeTrendLLM
from printfarm.agents.trend.signals import Window, detect, fake_insight, signal_of


def w(**kw) -> Window:
    return Window(scope=kw.pop("scope", "sku"), subject=kw.pop("subject", "HOOK"), niche=kw.pop("niche", "home"), **kw)


def test_rising_falling_and_quiet():
    up = signal_of(w(units_7d=13, units_prev_7d=5, units_28d=30, units_prev_28d=20))
    assert up.direction == "rising" and up.change_pct == 160.0 and up.strength == 1.0
    assert "13 sold in the last 7 days (5 the 7 days before)" in up.evidence[0]
    down = signal_of(w(units_7d=2, units_prev_7d=10))
    assert down.direction == "falling" and down.change_pct == -80.0 and down.strength == 0.8
    assert signal_of(w(units_7d=6, units_prev_7d=5)) is None               # +20%: noise
    assert signal_of(w(units_7d=2, units_prev_7d=1)) is None               # too few to tell


def test_new_demand_and_social_intent():
    new = signal_of(w(units_7d=4, units_prev_7d=0))
    assert new.direction == "rising" and new.change_pct is None and new.strength == 1.0
    social = signal_of(w(units_7d=1, units_prev_7d=1, leads_7d=4, leads_prev_7d=1))
    assert social.direction == "rising" and social.evidence[0].startswith("social intent: 4 leads")
    assert signal_of(w(leads_7d=1)) is None


def test_detect_orders_by_strength():
    found = detect([w(subject="A", units_7d=4, units_prev_7d=4), w(subject="B", units_7d=10, units_prev_7d=2),
                    w(subject="C", units_7d=0, units_prev_7d=0)])
    assert [s.subject for s in found] == ["B"] and found[0].topic == "sku:B"


async def test_the_fake_insight_uses_only_the_numbers():
    items, usage = await FakeTrendLLM().insights([{"niche": "home", "name": "Home décor", "units_7d": 12, "units_prev_7d": 5,
                                                   "leads_7d": 3, "rising": ["HOOK"]}])
    assert items[0].niche == "home" and "12 יחידות" in items[0].text and "HOOK" in items[0].text
    assert usage.cost > 0
    assert "עלייה" in fake_insight("Home", w(scope="niche", subject="home", units_7d=10, units_prev_7d=2), [])


# ---------------------------------------------------------------- gateway (Postgres faked)

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import KEY, csrf, gateway_client, login  # noqa: E402

commands = importlib.import_module("app.commands")
analytics = importlib.import_module("app.analytics")


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        db = c.app.state.db
        db.rows["coalesce(sum(revenue_agorot) FILTER"] = [
            {"niche": "home", "revenue": 50000, "cost": 10000, "units": 20, "hours": 10.0, "qc_checked": 20, "qc_failed": 3,
             "leads": 4, "revenue_prev": 30000, "cost_prev": 8000, "units_prev": 10, "leads_prev": 1}]
        db.rows["FROM catalog.niches ORDER BY sort"] = [
            {"slug": "home", "name": "Home décor", "color": 1, "target_margin_pct": 50.0, "archived": False, "sort": 10},
            {"slug": "kids", "name": "Kids & toys", "color": 2, "target_margin_pct": 50.0, "archived": False, "sort": 20}]
        db.values["SELECT max(day) FROM trend.signals"] = date(2026, 11, 18)
        db.values["SELECT max(rolled_at)"] = None
        db.rows["FROM trend.signals WHERE day = %s ORDER BY strength"] = [
            {"scope": "sku", "subject": "HOOK", "niche": "home", "direction": "rising", "strength": 1.0, "change_pct": 160.0,
             "evidence": ["13 sold in the last 7 days (5 the 7 days before)"]},
            {"scope": "niche", "subject": "home", "niche": "home", "direction": "rising", "strength": 0.6, "change_pct": 60.0,
             "evidence": []}]
        db.rows["FROM trend.insights ORDER BY niche"] = [{"niche": "home", "text": "עלייה יפה השבוע", "day": date(2026, 11, 18)}]
        db.rows["FROM coordinator.finished_goods"] = [{"sku": "HOOK", "free": 4}]
        yield c


def test_the_scorecard_compares_niches_and_suggests(client):
    r = client.get("/api/v1/analytics/scorecard?days=30").json()
    home = next(n for n in r["niches"] if n["niche"] == "home")
    assert (home["profit_agorot"], home["margin_pct"], home["profit_per_print_hour_agorot"]) == (40000, 80.0, 4000)
    assert home["demand_pct"] == 100.0 and home["qc_fail_pct"] == 15.0 and home["rising"] == ["HOOK"]
    assert home["insight"] == "עלייה יפה השבוע" and home["direction"] == "rising"
    kinds = {t["kind"]: t for t in home["suggestions"]}
    assert kinds["batch"]["quantity"] == 9 and kinds["batch"]["sku"] == "HOOK"       # 13 a week, 4 on the shelf
    assert "quality" in kinds
    kids = next(n for n in r["niches"] if n["niche"] == "kids")
    assert kids["revenue_agorot"] == 0 and kids["margin_pct"] is None and kids["suggestions"] == []


def test_downloads_csv_and_excel(client):
    csv = client.get("/api/v1/analytics/scorecard?format=csv")
    assert csv.status_code == 200 and csv.text.startswith("﻿") and "revenue ₪" in csv.text.splitlines()[0]
    assert "Home décor,500.0" in csv.text
    xlsx = client.get("/api/v1/analytics/scorecard?format=xlsx")
    assert xlsx.headers["content-type"].startswith("application/vnd.openxmlformats")
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
    assert ws.title == "Scorecard" and ws["A1"].value == "name" and ws["B2"].value == 500.0 and ws["A2"].value == "Home décor"
    assert client.get("/api/v1/analytics/scorecard?format=pdf").status_code == 422


def test_daily_rows_and_their_download(client):
    client.app.state.db.rows["FROM reporting.daily_niche WHERE day > %s AND day <= %s"] = [
        {"day": date(2026, 11, 18), "niche": "home", "units_sold": 3, "revenue_agorot": 12000, "cost_agorot": 2000,
         "print_hours": 2.5, "qc_checked": 3, "qc_failed": 0, "captures": 1, "leads": 1, "posted": 0}]
    assert client.get("/api/v1/analytics/daily?days=7").json()["rows"][0]["units_sold"] == 3
    csv = client.get("/api/v1/analytics/daily?days=7&format=csv").text
    assert "2026-11-18,home,3,120.0,20.0,2.5" in csv
    assert client.get("/api/v1/analytics/daily?by=week").status_code == 422


def test_signals_for_the_social_page(client):
    client.app.state.db.rows["FROM trend.signals s"] = [
        {"signal_id": "sig-1", "scope": "sku", "subject": "HOOK", "niche": "home", "direction": "rising", "strength": 1.0,
         "change_pct": 160.0, "evidence": ["13 sold"], "label": "Hook"}]
    r = client.get("/api/v1/trend/signals").json()
    assert r["day"] == "2026-11-18" and r["signals"][0]["label"] == "Hook"


def test_backfill_is_for_owners(client):
    assert commands.COMMANDS["analytics.backfill"].min_role == "owner"
    r = client.post("/api/v1/commands/analytics.backfill", headers={**csrf(client), "Idempotency-Key": KEY}, json={"days": 30})
    assert r.status_code == 202
    subject, published = client.app.state.bus.js.published[-1]
    assert subject == "cmd.trend.backfill" and published["data"]["requested_by"] == "admin"


def test_units_of_a_signal():
    assert analytics._units_7d({"evidence": ["13 sold in the last 7 days (5 the 7 days before)"]}) == 13
    assert analytics._units_7d({"evidence": ["social intent: 4 leads"]}) == 0
