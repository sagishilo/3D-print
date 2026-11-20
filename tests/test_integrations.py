"""Wave 2 / W2-0: the connector framework's pure rules (backoff, due, health, mapping diff, secrets), the demo
connector, the alert rules, and the gateway's integrations and notifications read side (Postgres faked)."""

from __future__ import annotations

import importlib
from datetime import UTC, date, datetime, timedelta

import pytest

from printfarm.agents.integrations import alerts
from printfarm.agents.integrations.connectors import DEMO_ORDERS, REGISTRY, DemoStore
from printfarm.agents.integrations.framework import (
    BACKOFF_CAP_S,
    Record,
    SecretSpec,
    SyncContext,
    SyncError,
    backoff_s,
    diff,
    health,
    is_due,
    live_blocked,
    next_run,
    read_env_file,
    secrets_status,
)

NOW = datetime(2026, 11, 20, 9, tzinfo=UTC)


def test_backoff_doubles_and_caps():
    assert backoff_s(0, 300) == 300
    assert [backoff_s(n, 300) for n in (1, 2, 3)] == [600, 1200, 2400]
    assert backoff_s(30, 300) == BACKOFF_CAP_S
    assert backoff_s(1, 300, jitter=1.0) == pytest.approx(660)
    assert next_run(NOW, 0, 60) - NOW >= timedelta(seconds=60)


def test_due_and_health():
    assert is_due(True, None, NOW) and is_due(True, NOW - timedelta(seconds=1), NOW)
    assert not is_due(True, NOW + timedelta(seconds=1), NOW) and not is_due(False, None, NOW)
    assert health(False, 0, None, None) == "off"
    assert health(True, 0, None, None) == "waiting"
    assert health(True, 0, NOW, NOW) == "ok"
    assert health(True, 1, NOW, NOW) == "failing"
    assert health(True, 3, NOW, NOW) == "down"


def test_the_mapping_diff_never_duplicates():
    a, b, c = Record("order", "A", "o-a", {"x": 1}), Record("order", "B", "o-b", {"x": 2}), Record("order", "C", "o-c", {})
    known = {("order", "A"): a.payload_hash, ("order", "B"): Record("order", "B", "o-b", {"x": 1}).payload_hash}
    d = diff([a, b, c, c], known)
    assert [r.external_id for r in d.create] == ["C"] and [r.external_id for r in d.update] == ["B"] and d.same == 1
    assert diff([a], {("order", "A"): a.payload_hash}).create == ()


def test_secrets_are_set_or_missing_never_values(tmp_path):
    f = tmp_path / "integrations.env"
    f.write_text("# a comment\nSHOP_TOKEN='abc'\nEMPTY=\nPLACEHOLDER=CHANGE_ME\n", encoding="utf-8")
    values = read_env_file(f)
    assert values == {"SHOP_TOKEN": "abc"}
    specs = (SecretSpec("SHOP_TOKEN", "token"), SecretSpec("SHOP_URL", "url"), SecretSpec("OPTIONAL", "x", required=False))
    assert secrets_status(specs, values) == {"SHOP_TOKEN": True, "SHOP_URL": False, "OPTIONAL": False}
    assert live_blocked(specs, values) == ["SHOP_URL"]
    assert read_env_file(tmp_path / "missing.env") == {}


def ctx(cursor=None, mode="fake", settings=None) -> SyncContext:
    return SyncContext(mode=mode, cursor=cursor or {}, settings=settings or {}, secrets={}, now=NOW)


async def test_the_demo_store_sees_one_more_order_each_sync_then_updates_the_last():
    demo = DemoStore()
    assert REGISTRY["demo"].kind == "demo"
    r1 = await demo.sync(ctx())
    assert [x.external_id for x in r1.records] == ["D-1001"] and r1.cursor == {"seen": 1, "runs": 1}
    cursor = r1.cursor
    for _ in range(len(DEMO_ORDERS)):
        r = await demo.sync(ctx(cursor))
        cursor = r.cursor
    assert len(r.records) == len(DEMO_ORDERS)
    nxt = await demo.sync(ctx(cursor))
    assert nxt.records[-1].payload_hash != r.records[-1].payload_hash        # the last order changed status
    assert nxt.records[0].payload_hash == r.records[0].payload_hash
    with pytest.raises(SyncError, match="no live service"):
        await demo.sync(ctx(mode="live"))
    with pytest.raises(SyncError, match="set to fail"):
        await demo.sync(ctx(settings={"fail": True}))


def test_alert_rules_are_critical_only():
    today = date(2026, 11, 20)
    safety = alerts.for_event("printer.pause.unconfirmed", {"printer_id": "x1", "plate_id": "p1", "reason": "spaghetti",
                                                           "last_status": "printing"}, today)
    assert safety.severity == "critical" and safety.link == "/fleet?printer=x1" and safety.dedupe_key == "safety:x1:p1"
    assert alerts.for_event("fleet.plate.finished", {"succeeded": True, "plate_id": "p"}, today) is None
    failed = alerts.for_event("fleet.plate.finished", {"succeeded": False, "plate_id": "p", "printer_id": "x1",
                                                       "material": "PLA", "color": "black"}, today)
    assert failed.topic == "print_failed"
    assert alerts.for_event("printer.failure.detected", {"severity": "warning"}, today) is None
    assert alerts.for_event("inventory.alert.raised", {"severity": "warning", "alert_kind": "low_stock"}, today) is None
    att = alerts.for_event("order.order.needs_attention", {"order_id": "o1", "reason": "model failed"}, today)
    assert att.dedupe_key == "attention:o1:2026-11-20"
    down = alerts.for_event("integrations.sync.finished", {"ok": False, "failures": 3, "connector_id": "demo",
                                                           "error": "boom"}, today)
    assert down.min_role == "owner" and "boom" in down.body
    assert alerts.for_event("integrations.sync.finished", {"ok": False, "failures": 4}, today) is None


# ---------------------------------------------------------------- gateway (Postgres faked)

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import KEY, PASSWORD, add_user, csrf, gateway_client, login  # noqa: E402

commands = importlib.import_module("app.commands")


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        c.app.state.db.rows["FROM integrations.connectors c LEFT JOIN"] = [{
            "connector_id": "demo", "kind": "demo", "label": "Demo store", "enabled": True, "mode": "fake", "interval_s": 60,
            "secrets_status": {"DEMO_STORE_TOKEN": False}, "updated_by": "admin", "updated_at": NOW, "last_run_at": NOW,
            "last_ok_at": NOW, "last_error": None, "last_error_at": None, "consecutive_failures": 0, "next_run_at": NOW,
            "runs": 4, "items": 5, "mapped": 5}]
        yield c


def test_connectors_show_health_and_only_whether_secrets_are_set(client):
    c = client.get("/api/v1/integrations").json()["connectors"][0]
    assert c["health"] == "ok" and c["secrets_missing"] == ["DEMO_STORE_TOKEN"] and c["mapped"] == 5
    h = client.get("/api/v1/integrations/health").json()
    assert h["down"] == 0 and "secrets_status" not in h["connectors"][0]
    assert client.get("/api/v1/integrations/bad id!/runs").status_code in (400, 404)


def test_integrations_are_for_owners_and_notifications_follow_the_role(client):
    import asyncio

    asyncio.run(add_user(client.app, "noa", "operator"))
    client.app.state.db.rows["FROM notify.outbox"] = [
        {"id": 2, "topic": "integration", "severity": "warning", "title": "Connector demo keeps failing", "body": "", "link": "",
         "min_role": "owner", "status": "dashboard", "channels": [], "attempts": 0, "error": None, "created_at": NOW,
         "sent_at": None},
        {"id": 1, "topic": "safety", "severity": "critical", "title": "x1 did not pause", "body": "", "link": "/fleet",
         "min_role": "operator", "status": "dashboard", "channels": [], "attempts": 0, "error": None, "created_at": NOW,
         "sent_at": None}]
    assert len(client.get("/api/v1/notifications").json()["notifications"]) == 2
    client.post("/api/v1/auth/login", json={"username": "noa", "password": PASSWORD})         # an operator
    assert [n["id"] for n in client.get("/api/v1/notifications").json()["notifications"]] == [1]
    assert client.get("/api/v1/integrations").status_code == 403
    assert client.get("/api/v1/integrations/health").status_code == 200
    for name in ("integrations.configure", "integrations.run"):
        assert commands.COMMANDS[name].min_role == "owner"


def test_configure_and_run_now_reach_the_agent(client):
    r = client.post("/api/v1/commands/integrations.configure", headers={**csrf(client), "Idempotency-Key": KEY},
                    json={"connector_id": "demo", "enabled": True, "mode": "fake", "interval_s": 60})
    assert r.status_code == 202
    subject, published = client.app.state.bus.js.published[-1]
    assert subject == "cmd.integrations.configure" and published["data"]["changed_by"] == "admin"
    bad = client.post("/api/v1/commands/integrations.configure", headers={**csrf(client), "Idempotency-Key": "test-key-0002"},
                      json={"connector_id": "demo", "interval_s": 5})
    assert bad.status_code == 422
