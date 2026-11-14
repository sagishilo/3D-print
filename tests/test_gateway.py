"""Gateway: command catalog, WebSocket topic rules, auth and the command endpoint (NATS and Postgres are faked)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

import httpx  # noqa: E402
from gateway_support import (  # noqa: E402 - sets the gateway's environment and sys.path, then re-exports its helpers
    COMMANDS,
    KEY,
    build_command,
    command_id_from,
    csrf,
    gateway_client,
    login,
    ws_topic_allowed,
)
from pydantic import ValidationError  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from printfarm.contracts import REGISTRY, Kind, subject_matches  # noqa: E402

# ---------------------------------------------------------------- catalog (pure)


def test_every_command_name_maps_to_a_registered_contract():
    registered = set(REGISTRY.values())
    for name, spec in COMMANDS.items():
        assert spec.model in registered, name
        root = spec.model.subject.split(".")[0]
        assert root in {"cmd", "evt"}, name
        if root == "cmd":
            assert spec.model.kind == Kind.COMMAND
        if spec.actor_field:
            assert spec.actor_field in spec.model.model_fields, name


def test_actor_always_comes_from_the_session():
    env = build_command("approvals.decide", {"approval_id": "apr-1", "approved": True, "decided_by": "mallory"}, "admin", KEY)
    assert env.payload().decided_by == "admin"
    assert env.id == KEY and env.idempotencykey == KEY and env.source == "/gateway"
    assert env.nats_subject() == "cmd.coordinator.decide_approval"


def test_invalid_bodies_are_rejected_before_the_bus():
    with pytest.raises(ValidationError):
        build_command("payments.record", {"amount_agorot": -5}, "admin", KEY)
    with pytest.raises(ValidationError):
        build_command("payments.record", {"amount_agorot": 100, "bogus": 1}, "admin", KEY)
    with pytest.raises(ValueError):
        build_command("config.set", {"key": "Not A Key!", "value": True}, "admin", KEY)


def test_only_live_known_settings_can_be_set():
    env = build_command("config.set", {"key": "safety.auto_pause", "value": False}, "admin", KEY)
    assert env.payload().changed_by == "admin" and env.nats_subject() == "cmd.coordinator.set_config"
    with pytest.raises(ValueError, match="unknown setting"):
        build_command("config.set", {"key": "made.up", "value": True}, "admin", KEY)
    with pytest.raises(ValueError, match="arrives in phase 9"):
        build_command("config.set", {"key": "fleet.quiet_hours", "value": True}, "admin", KEY)
    with pytest.raises(ValueError, match="takes a bool value"):
        build_command("config.set", {"key": "safety.auto_pause", "value": "off"}, "admin", KEY)
    with pytest.raises(ValueError, match="unknown setting"):
        build_command("config.set", {"key": ["safety.auto_pause"], "value": True}, "admin", KEY)


def test_printer_commands_expire_and_others_do_not():
    assert build_command("printers.pause", {"printer_id": "p1", "reason": "x"}, "admin", KEY).deadline is not None
    assert build_command("payments.record", {"amount_agorot": 100}, "admin", KEY).deadline is None


def test_new_order_id_is_derived_from_the_command_id():
    a = build_command("orders.create", {"text": "Noa"}, "admin", "retry-key-123")
    b = build_command("orders.create", {"text": "Noa"}, "admin", "retry-key-123")
    assert a.payload().order_id == b.payload().order_id == "ord-retry-key-"
    assert a.correlationid == a.payload().order_id


def test_idempotency_key_rules():
    assert command_id_from("abcd-1234_XY") == "abcd-1234_XY"
    assert len(command_id_from(None)) == 32
    with pytest.raises(ValueError):
        command_id_from("short")
    with pytest.raises(ValueError):
        command_id_from("has spaces in it")


# ---------------------------------------------------------------- WebSocket topics (pure)


@pytest.mark.parametrize("topic", ["evt.>", "evt.fleet.job.>", "tlm.*.state", "tlm.voron-01.state", "dlq.>",
                                   "evt.order.payment.recorded"])
def test_ws_allows_events_telemetry_and_dead_letters(topic):
    assert ws_topic_allowed(topic)


@pytest.mark.parametrize("topic", ["cmd.>", ">", "*.>", "*", "qry.inventory.availability", "evt", "evt..x", "evt.>.x",
                                   "evt.a b", "", None, 42, "print.job.>", "order.>", "_INBOX.>", "$JS.API.>"])
def test_ws_refuses_everything_else(topic):
    assert not ws_topic_allowed(topic)


def test_no_command_or_query_subject_is_reachable_over_the_websocket():
    """Regression test for the stack's prefix allow-list, which forwarded print.job.create & co."""
    private = [m.subject for m in REGISTRY.values() if m.subject.split(".")[0] in {"cmd", "qry"}]
    assert private
    for subject in private:
        assert not ws_topic_allowed(subject)
        for broad in ("evt.>", "tlm.>", "dlq.>"):
            assert not subject_matches(broad, subject)


# ---------------------------------------------------------------- endpoints (fakes in gateway_support.py)


@pytest.fixture
def client():
    with gateway_client() as c:
        yield c


def test_login_lockout_after_five_failures(client):
    for left in (4, 3, 2, 1, 0):
        r = login(client, "wrong")
        assert r.status_code == 401 and f"{left} attempts left" in r.json()["detail"]
    assert login(client).status_code == 429  # even the right password waits out the lockout


def test_login_refresh_and_me(client):
    assert login(client).status_code == 200
    assert {"pf_access", "pf_refresh", "pf_csrf"} <= set(client.cookies.keys())
    assert client.get("/api/v1/auth/me").json()["user"] == "admin"
    assert client.post("/api/v1/auth/refresh").status_code == 200   # rotates the refresh token


def test_non_ascii_credentials_are_a_401_not_a_crash(client):
    r = client.post("/api/v1/auth/login", json={"username": "מנהל", "password": "סיסמה"})
    assert r.status_code == 401


def test_commands_need_login_and_csrf(client):
    assert client.post("/api/v1/commands/printers.pause_all", json={}).status_code == 401
    login(client)
    assert client.post("/api/v1/commands/printers.pause_all", json={}).status_code == 403
    assert client.post("/api/v1/commands/printers.pause_all", json={}, headers={"x-csrf-token": "forged"}).status_code == 403
    raw_utf8 = {"x-csrf-token": "זיוף".encode()}  # non-ASCII header bytes must be a 403, not a crash in compare_digest
    assert client.post("/api/v1/commands/printers.pause_all", json={}, headers=raw_utf8).status_code == 403


def test_command_is_validated_published_and_audited(client):
    login(client)
    r = client.post("/api/v1/commands/payments.record", json={"order_id": "ord-1", "amount_agorot": 35400, "method": "bit"},
                    headers={**csrf(client), "Idempotency-Key": KEY})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["command_id"] == KEY and body["subject"] == "cmd.coordinator.record_payment" and not body["duplicate"]
    subject, env = client.app.state.bus.js.published[-1]
    assert subject == "cmd.coordinator.record_payment"
    assert env["id"] == KEY and env["data"]["recorded_by"] == "admin" and env["data"]["amount_agorot"] == 35400
    audit = client.app.state.db.audit[KEY]
    assert (audit["actor"], audit["subject"], audit["outcome"]) == ("admin", subject, "published")
    assert audit["payload"]["amount_agorot"] == 35400


def test_retry_with_the_same_key_is_not_published_twice(client):
    login(client)
    headers = {**csrf(client), "Idempotency-Key": KEY}
    first = client.post("/api/v1/commands/printers.pause_all", json={}, headers=headers)
    second = client.post("/api/v1/commands/printers.pause_all", json={}, headers=headers)
    assert first.json()["duplicate"] is False and second.json()["duplicate"] is True
    assert len(client.app.state.bus.js.published) == 1


def test_bad_command_bodies(client):
    login(client)
    h = csrf(client)
    assert client.post("/api/v1/commands/payments.record", json={"amount_agorot": -1}, headers=h).status_code == 422
    assert client.post("/api/v1/commands/no.such.command", json={}, headers=h).status_code == 404
    assert client.post("/api/v1/commands/payments.record", content=b"[1, 2]", headers=h).status_code == 400
    assert client.post("/api/v1/commands/payments.record", json={"amount_agorot": 1},
                       headers={**h, "Idempotency-Key": "bad key"}).status_code == 400


def test_printer_shortcut_endpoint(client):
    login(client)
    r = client.post("/api/v1/commands/printers/voron-01/pause", headers=csrf(client))
    assert r.status_code == 202
    subject, env = client.app.state.bus.js.published[-1]
    assert subject == "cmd.telemetry.pause_print"
    assert env["data"] == {"printer_id": "voron-01", "reason": "paused from the dashboard", "requested_by": "admin"}
    assert env["deadline"]
    assert client.post("/api/v1/commands/printers/Voron 01/pause", headers=csrf(client)).status_code == 400
    assert client.post("/api/v1/commands/printers/voron-01/explode", headers=csrf(client)).status_code == 400


class FailingRelay:
    """Stands in for go2rtc: every request raises ``exc``."""

    def __init__(self, exc: Exception):
        self.exc = exc

    async def get(self, *a, **kw):
        raise self.exc

    def build_request(self, *a, **kw):
        return None

    async def send(self, *a, **kw):
        raise self.exc


class EmptyFrameRelay:
    """go2rtc answering 200 with no picture (seen when the camera source fails)."""

    async def get(self, *a, **kw):
        return httpx.Response(200, content=b"")


def test_empty_snapshot_from_the_relay_is_a_502(client):
    login(client)
    client.app.state.http = EmptyFrameRelay()
    assert client.get("/api/v1/cameras/bambu-02/snapshot.jpg").status_code == 502


@pytest.mark.parametrize("exc,status", [(httpx.ReadTimeout("slow"), 504), (httpx.ConnectError("refused"), 502)])
def test_camera_relay_errors_are_502_or_504_not_500(client, exc, status):
    login(client)
    client.app.state.http = FailingRelay(exc)
    assert client.get("/api/v1/cameras/bambu-02/snapshot.jpg").status_code == status
    assert client.get("/api/v1/cameras/bambu-02/mjpeg").status_code == status
    assert client.get("/api/v1/cameras").status_code == status


def test_websocket_requires_login(client):
    with client.websocket_connect("/ws") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    assert closed.value.code == 4401


def test_websocket_refuses_command_topics(client):
    login(client)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "topics": ["evt.>", "cmd.>", "print.job.>", "tlm.*.state"]})
        reply = ws.receive_json()
    assert reply["topics"] == ["evt.>", "tlm.*.state"]
    assert reply["refused"] == ["cmd.>", "print.job.>"]
    assert client.app.state.bus.js.subscribed == ["evt.>", "tlm.*.state"]  # JetStream ordered consumers, not core subs
