"""Gateway: the filament read side and commands (Postgres faked)."""

from __future__ import annotations

import pytest

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


MATERIAL = {"material": "PLA", "color": "black", "grams_per_spool": 1000, "cost_per_spool_agorot": 8500,
            "lead_time_days": 4, "safety_stock_days": 2, "review_period_days": 14, "baseline_daily_grams": 100,
            "auto_approve_limit_agorot": 20000, "min_stock_grams": 1000, "reorder_mode": "manual", "supplier": "Bambu Store",
            "spools_loaded": 1, "spools_stock": 1, "on_order_spools": 0, "reserved": 120.0}


def test_reorder_list_applies_the_rule_to_every_material(client):
    db = client.app.state.db
    db.rows["FROM inventory.materials m"] = [
        {**MATERIAL, "on_hand": 900.0, "used_14d": 14 * 150.0},
        {**MATERIAL, "color": "white", "reorder_mode": "auto", "on_hand": 5000.0, "used_14d": 0.0}]
    black, white = client.get("/api/v1/inventory/materials").json()["materials"]
    assert black["state"] == "triggered" and black["below_min"] and black["daily_grams"] == 150
    assert black["suggested_spools"] == 4 and black["needs_approval"]         # manual mode always asks
    assert black["days_left"] == 6.0 and black["reserved_grams"] == 120.0
    assert white["state"] == "ok" and white["suggested_spools"] == 0 and white["days_to_trigger"] == 34.0


def test_spools_alerts_pos_usage(client):
    db = client.app.state.db
    db.rows["FROM inventory.spools"] = [{"spool_id": "SP-1", "status": "loaded", "printer_id": "voron-01", "slot": "T0"}]
    db.rows["FROM inventory.reservations"] = [{"plate_id": "plate-1", "printer_id": "voron-01", "grams": 80.0}]
    db.rows["FROM inventory.alerts"] = [{"alert_id": "inv-1", "kind": "spool_short", "message": "load another spool"}]
    db.rows["sum(total_agorot)"] = [{"total": 53400, "n": 2}]
    db.rows["FROM inventory.purchase_orders"] = [{"po_id": "po-1", "status": "pending_approval"}]
    db.rows["FROM inventory.ledger"] = [{"day": "2026-11-13", "material": "PLA", "color": "black", "grams": 930.5}]
    sp = client.get("/api/v1/inventory/spools").json()
    assert sp["spools"][0]["slot"] == "T0" and sp["reservations"][0]["grams"] == 80.0
    assert client.get("/api/v1/inventory/alerts").json()["alerts"][0]["kind"] == "spool_short"
    pos = client.get("/api/v1/inventory/pos").json()
    assert pos["spend_30d_agorot"] == 53400 and pos["ordered_30d"] == 2 and pos["pos"][0]["po_id"] == "po-1"
    assert client.get("/api/v1/inventory/usage").json()["usage"][0]["grams"] == 930.5
    assert client.get("/api/v1/inventory/usage?days=400").status_code == 422


def test_inventory_commands():
    env = build_command("spools.register", {"material": "PETG", "color": "clear", "initial_grams": 1000, "tag": "QR-77"},
                        "admin", KEY)
    assert env.payload().spool_id == "QR-77" and env.payload().registered_by == "admin"   # a scanned label is the id
    env = build_command("spools.register", {"material": "PLA", "color": "red", "initial_grams": 750}, "admin", "abcd1234ef")
    assert env.payload().spool_id == "SP-ABCD1234"
    with pytest.raises(ValidationError):
        build_command("spools.adjust", {"spool_id": "SP-1", "remaining_grams": -5}, "admin", KEY)
    with pytest.raises(ValidationError):
        build_command("inventory.rule", {"material": "PLA", "color": "black", "mode": "sometimes"}, "admin", KEY)
    rule = build_command("inventory.rule", {"material": "PLA", "color": "black", "min_stock_grams": 2000}, "admin", KEY)
    assert rule.payload().mode is None and rule.nats_subject() == "cmd.inventory.set_reorder_rule"
    assert build_command("po.update", {"po_id": "po-1", "spools": 3}, "admin", KEY).payload().updated_by == "admin"


def test_inventory_needs_a_session():
    with gateway_client() as anonymous:
        for path in ("/api/v1/inventory/spools", "/api/v1/inventory/materials", "/api/v1/inventory/alerts"):
            assert anonymous.get(path).status_code == 401, path
