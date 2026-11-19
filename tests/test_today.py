"""Wave 1 / M7: Today. The pure builders (each source of work becomes items with one action), the ranking and its
role and niche filters, and the endpoint (Postgres faked)."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import gateway_client, login  # noqa: E402

today = importlib.import_module("app.today")
NOW = datetime(2026, 11, 19, 12, tzinfo=UTC)


def ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def test_a_finished_printer_waits_for_its_bed():
    items = today.beds([{"printer_id": "x1", "online": True, "busy": False, "bed_clear": False},
                        {"printer_id": "x2", "online": True, "busy": True, "bed_clear": False},
                        {"printer_id": "x3", "online": False, "busy": False, "bed_clear": False}])
    assert [i.id for i in items] == ["bed:x1"] and items[0].priority == "now"
    assert items[0].action == {"type": "command", "name": "printers.clear_bed", "body": {"printer_id": "x1"},
                               "label": "Bed cleared", "confirm": "Is the bed of x1 empty and clean?"}


def test_orders_to_confirm_check_ship_and_unstick():
    cards = [
        {"order_id": "o1", "state": "LEAD", "customer_name": "Noa", "stage_since": ago(3), "niche": "gifts"},
        {"order_id": "o2", "state": "QC", "qc": None, "customer_name": "Dan", "stage_since": ago(1)},
        {"order_id": "o3", "state": "QC", "qc": "passed", "customer_name": "Gal", "stage_since": ago(1)},
        {"order_id": "o4", "state": "SCHEDULING", "stuck": True, "status": "2 unit(s) waiting for a plate",
         "customer_name": "Lior", "stage_since": ago(5)},
        {"order_id": "o5", "state": "NEEDS_ATTENTION", "status": "model failed: item 1", "customer_name": "Ori",
         "stage_since": ago(1)},
        {"order_id": "o6", "state": "PRINTING", "customer_name": "Yael", "stage_since": ago(9)},     # printers drive it
    ]
    items = {i.id: i for i in today.orders(cards, NOW)}
    assert set(items) == {"lead:o1", "qc:o2", "ship:o3", "stuck:o4", "attention:o5"}
    assert items["lead:o1"].action["name"] == "orders.advance" and items["lead:o1"].action["body"]["to_state"] == "RECEIVED"
    assert items["attention:o5"].priority == "now" and items["qc:o2"].action == {"type": "link", "to": "/orders?order=o2",
                                                                                 "label": "Check"}
    assert items["stuck:o4"].why.startswith("2 unit(s) waiting")


def test_stock_drying_and_restock():
    items = today.stock_alerts([
        {"alert_id": "a1", "kind": "spool_short", "message": "x1: plate p needs ~30 g", "opened_at": ago(1)},
        {"alert_id": "a2", "kind": "low_item", "message": "Gift box S: 4 pcs left, below the minimum of 20: reorder",
         "opened_at": ago(30)}], NOW)
    assert [(i.priority, i.title) for i in items] == [("now", "x1: plate p needs ~30 g"), ("week", "Gift box S")]
    restock = today.shelf([{"sku": "HOOK", "name": "Hook", "niche": "home", "on_hand": 3, "reserved": 2, "min_stock": 5,
                            "in_production": 1}])[0]
    assert restock.action["body"]["items"][0]["quantity"] == 3 and restock.niche == "home"
    dry = today.drying([{"spool_id": "S1", "material": "PETG", "color": "black", "opened_at": NOW - timedelta(days=30),
                         "dried_at": None}, {"spool_id": "S2", "material": "PLA", "color": "white",
                                             "opened_at": NOW - timedelta(days=30), "dried_at": None}], NOW)
    assert [i.id for i in dry] == ["dry:S1"]


def test_money_and_technical_items_are_for_operators():
    late = today.receivables([{"due_agorot": 9000, "shipped_at": NOW - timedelta(days=20)},
                              {"due_agorot": 5000, "shipped_at": NOW - timedelta(days=2)}], NOW)
    assert late[0].count == 1 and "₪90" in late[0].title and late[0].visible_to == "operator"
    tech = today.technical(3, [{"label": "Coordinator", "status": "down", "tier": "core"},
                               {"label": "Trend Analyst", "status": "down", "tier": "optional"}])
    assert {i.id for i in tech} == {"dlq", "agents"} and all(i.expert for i in tech)
    assert "Trend Analyst" not in next(i for i in tech if i.id == "agents").title
    assert today.backup({"state": "ok"}) == [] and today.backup({"state": "problem", "problems": ["old"]})[0].min_role == "owner"


def test_rank_orders_by_urgency_and_age_and_filters_role_and_niche():
    items = [
        *today.beds([{"printer_id": "x1", "online": True, "busy": False, "bed_clear": False}]),
        *today.orders([{"order_id": "o1", "state": "LEAD", "customer_name": "Noa", "stage_since": ago(1), "niche": "gifts"},
                       {"order_id": "o2", "state": "LEAD", "customer_name": "Dan", "stage_since": ago(30), "niche": "home"}],
                      NOW),
        *today.margins(2),
    ]
    ranked = today.rank(items, role="operator")
    assert [i.id for i in ranked] == ["bed:x1", "lead:o2", "lead:o1", "margins"]          # now, then the older lead
    assert "margins" not in [i.id for i in today.rank(items, role="viewer")]
    gifts = today.rank(items, role="operator", niche="gifts")
    assert [i.id for i in gifts] == ["bed:x1", "lead:o1", "margins"]                       # farm-wide items stay


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def test_the_endpoint_groups_items_and_skips_a_failing_source(client):
    db = client.app.state.db
    db.rows["FROM fleet.printers"] = [{"printer_id": "x1", "online": True, "busy": False, "bed_clear": False}]
    db.rows["FROM coordinator.approvals"] = [{"approval_id": "apr-1", "action": "inventory.reorder", "requested_by": "inventory",
                                              "summary": "Order 4 x PLA black spools (~340.00 ILS)", "created_at": ago(2)}]
    r = client.get("/api/v1/today").json()
    assert [i["id"] for i in r["now"] if not i["expert"]] == ["bed:x1"]
    assert next(i for i in r["now"] if i["id"] == "agents")["expert"] is True       # no heartbeats in the fake bus
    assert any(i["id"] == "approval:apr-1" and i["action"]["type"] == "approvals" for i in r["today"])
    assert r["count"] == len(r["now"]) + len(r["today"]) + len(r["week"])
    assert client.get("/api/v1/today?niche=Bad!").status_code == 400


def test_today_needs_a_session():
    with gateway_client() as anonymous:
        assert anonymous.get("/api/v1/today").status_code == 401
