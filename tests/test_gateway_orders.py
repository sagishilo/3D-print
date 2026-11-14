"""Gateway: the Order Pipeline read side (Postgres faked)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import gateway_client, login  # noqa: E402


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def state(client):
    return client.app.state


def saga_row(order_id, saga_state, ctx, *, since_hours=0.5, **over):
    now = datetime.now(UTC)
    return {"order_id": order_id, "customer_name": "Noa", "customer_ref": "", "channel": "whatsapp", "due_by": None,
            "notes": "", "subtotal_agorot": 14500, "discount_agorot": 0, "shipping_agorot": 0, "total_agorot": 14500,
            "units": 3, "created_by": "admin", "created_at": now - timedelta(days=1), "state": saga_state, "context": ctx,
            "updated_at": now, "stage_since": now - timedelta(hours=since_hours), "paid_agorot": 5000, **over}


ITEMS = [{"order_id": "o1", "item_id": "1", "product": "nameplate", "text": "Noa", "material": "PLA", "color": "black",
          "quantity": 2, "unit_price_agorot": 5000, "line_total_agorot": 10000},
         {"order_id": "o1", "item_id": "2", "product": "nameplate", "text": "Dan", "material": "PLA", "color": "white",
          "quantity": 1, "unit_price_agorot": 4500, "line_total_agorot": 4500}]


def test_board_cards_say_what_each_order_waits_for(client):
    db = state(client).db
    personalizing = {"items": {"1": {"model_status": "ready"}, "2": {"model_status": "generating"}},
                     "units": {"1.1": {"status": "pending"}, "1.2": {"status": "pending"}, "2.1": {"status": "pending"}}}
    attention = {"items": {}, "units": {"1.1": {"status": "failed"}},
                 "attention": {"from": "PRINTING", "reason": "unit 1.1 failed 2 times: spaghetti"}}
    db.rows["JOIN coordinator.sagas"] = [
        saga_row("o1", "PERSONALIZING", personalizing, since_hours=3),
        saga_row("o2", "NEEDS_ATTENTION", attention),
        saga_row("o3", "PRINTING", {"units": {"1.1": {"status": "printed"}, "1.2": {"status": "printing"}}}, since_hours=5)]
    db.rows["FROM coordinator.order_items"] = ITEMS
    db.rows["FROM fleet.jobs j"] = [{"order_id": "o3", "plate_id": "plate-9", "printer_id": "voron-01", "status": "printing"}]
    cards = {c["order_id"]: c for c in client.get("/api/v1/orders").json()["orders"]}
    o1, o2, o3 = cards["o1"], cards["o2"], cards["o3"]
    assert o1["status"] == "models ready 1/2 · generating" and o1["stuck"] is True
    assert [i["text"] for i in o1["items"]] == ["Noa", "Dan"] and o1["items"][1]["model"]["status"] == "generating"
    assert o2["stage"] == "PRINTING" and o2["state"] == "NEEDS_ATTENTION" and o2["status"].startswith("unit 1.1 failed")
    assert o3["status"] == "printed 1/2 units" and o3["stuck"] is False     # printers drive this stage: never "stuck"
    assert o3["plates"] == [{"plate_id": "plate-9", "printer_id": "voron-01", "status": "printing"}]
    assert o1["paid_agorot"] == 5000 and o1["total_agorot"] == 14500


def test_order_details_and_errors(client):
    db = state(client).db
    ctx = {"items": {"1": {"text": "Noa", "model_status": "ready"}, "2": {"text": "Dan", "model_status": "ready"}},
           "units": {"1.1": {"status": "printed", "attempt": 1}, "2.1": {"status": "printed", "attempt": 2}},
           "qc": {"status": "passed"}}
    db.rows["JOIN coordinator.sagas"] = [saga_row("o1", "QC", ctx)]
    db.rows["FROM coordinator.order_items"] = ITEMS
    db.rows["FROM coordinator.qc_checks WHERE order_id"] = [{"passed": False, "units_failed": 1, "units_checked": 3}]
    d = client.get("/api/v1/orders/o1").json()
    assert d["status"] == "passed · ready to ship" and d["pricing"]["lines"][1]["line_total_agorot"] == 4500
    assert d["unit_status"][1] == {"unit": "2.1", "status": "printed", "attempt": 2, "item_text": "Dan"}
    assert d["qc_checks"][0]["units_failed"] == 1
    db.rows.pop("JOIN coordinator.sagas")
    assert client.get("/api/v1/orders/nope").status_code == 404
    assert client.get("/api/v1/orders/bad%20id").status_code == 400


def test_pipeline_stats(client):
    db = state(client).db
    db.rows["FROM coordinator.qc_checks WHERE checked_at"] = [{"failed": 2, "checked": 80}]
    db.rows["h.to_state = 'SHIPPED'"] = [{"cur": 2.6 * 86400, "prev": 3.0 * 86400}]
    assert client.get("/api/v1/orders/stats").json() == {
        "qc_failed_units_7d": 2, "qc_checked_units_7d": 80, "lead_to_ship_days": 2.6, "lead_to_ship_days_prev": 3.0}


def test_orders_need_a_session():
    with gateway_client() as anonymous:
        for path in ("/api/v1/orders", "/api/v1/orders/stats", "/api/v1/orders/plates", "/api/v1/orders/o1"):
            assert anonymous.get(path).status_code == 401, path
