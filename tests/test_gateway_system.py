"""Gateway read side for the dashboard: live feed resume/replay, System & Agents, DLQ, settings, printers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import csrf, fake_msg, gateway_client, login  # noqa: E402
from nats.js.api import DeliverPolicy  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from printfarm.contracts import new_message  # noqa: E402
from printfarm.contracts.messages import DeadLettered, PausePrint  # noqa: E402


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def state(client):
    return client.app.state


# ---------------------------------------------------------------- live feed: origin, resume, replay


def test_websocket_from_a_foreign_origin_is_closed(client):
    with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws, \
            pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    assert closed.value.code == 4403


@pytest.mark.parametrize("origin", ["https://localhost", "https://192.168.50.10", "http://localhost:5173"])
def test_websocket_from_the_dashboard_origins_is_accepted(client, origin):
    with client.websocket_connect("/ws", headers={"origin": origin}) as ws:
        ws.send_json({"op": "sub", "topics": ["evt.>"]})
        assert ws.receive_json()["topics"] == ["evt.>"]


def test_resume_starts_after_the_clients_cursor(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "topics": ["evt.>", "tlm.*.state"], "resume": {"EVENTS": 7}})
        reply = ws.receive_json()
    assert reply["resumed"] == {"EVENTS": {"from": 8, "gap": 3, "truncated": False}}
    events_cfg, telemetry_cfg = state(client).bus.js.configs
    assert events_cfg.deliver_policy == DeliverPolicy.BY_START_SEQUENCE and events_cfg.opt_start_seq == 8
    assert telemetry_cfg.deliver_policy == DeliverPolicy.NEW  # no cursor for that stream: live only


def test_resume_far_behind_is_truncated_and_a_reset_stream_is_reported(client):
    state(client).bus.js.streams["EVENTS"].state.last_seq = 10_000
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "topics": ["evt.>"], "resume": {"EVENTS": 3}})
        far = ws.receive_json()["resumed"]["EVENTS"]
    assert far["truncated"] and far["from"] == 10_000 - 2000 + 1
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "topics": ["evt.>"], "resume": {"EVENTS": 99_999}})
        assert ws.receive_json()["resumed"] == {"EVENTS": {"reset": True}}


def test_replay_streams_history_then_an_end_marker(client):
    js = state(client).bus.js
    js.stored["evt.printer.>"] = [
        fake_msg("evt.printer.pause.confirmed", "EVENTS", 5, {"id": "a", "time": "2026-11-14T09:00:01Z"}, pending=1),
        fake_msg("evt.printer.failure.detected", "EVENTS", 6, {"id": "b", "time": "2026-11-14T09:00:00Z"}, pending=0),
    ]
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "replay", "id": "r1", "topics": ["evt.printer.>", "cmd.>"], "since": "2026-11-14T08:00:00Z"})
        first, second, end = ws.receive_json(), ws.receive_json(), ws.receive_json()
    assert [first["seq"], second["seq"]] == [6, 5]  # in time order across the stream
    assert first["type"] == "replay" and first["id"] == "r1" and first["stream"] == "EVENTS"
    assert end == {**end, "type": "replay_end", "id": "r1", "count": 2, "truncated": False}
    assert js.subscribed == ["evt.printer.>"]  # cmd.> was not even looked at
    assert js.configs[0].deliver_policy == DeliverPolicy.BY_START_TIME


def test_replay_needs_a_start_time(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "replay", "id": "r2", "topics": ["evt.>"]})
        assert ws.receive_json()["error"] == "need topics and since"


# ---------------------------------------------------------------- System & Agents


def beat(agent: str, age_s: float) -> bytes:
    at = (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat()
    return json.dumps({"agent": agent, "version": "0.3.0", "at": at, "uptime_s": 60, "host": "c1"}).encode()


def test_agents_health_version_and_queue(client):
    bus = state(client).bus
    bus.kv["heartbeats"] = {"coordinator": beat("coordinator", 2), "fleet": beat("fleet", 27)}
    bus.js.consumers = {"EVENTS": [SimpleNamespace(name="coordinator__evt_order_order_received", num_pending=3,
                                                   num_ack_pending=1),
                                   SimpleNamespace(name="ephemeral-dashboard", num_pending=50, num_ack_pending=0)],
                        "COMMANDS": [SimpleNamespace(name="coordinator__cmd_coordinator_decide_approval", num_pending=2,
                                                     num_ack_pending=0)]}
    agents = {a["name"]: a for a in client.get("/api/v1/system/agents").json()["agents"]}
    assert agents["coordinator"]["status"] == "healthy" and agents["coordinator"]["queue"] == 6
    assert agents["coordinator"]["version"] == "0.3.0"
    assert agents["fleet"]["status"] == "late"
    assert agents["telemetry"]["status"] == "down" and agents["telemetry"]["queue"] is None
    assert agents["social"]["status"] == "planned"


def test_overview_combines_nats_postgres_and_dlq(client):
    st = state(client)
    st.nats_http.routes = {"/varz": {"version": "2.10.29", "uptime": "1h", "connections": 9, "in_msgs": 100,
                                     "out_msgs": 50, "mem": 1},
                           "/jsz": {"streams": 7, "consumers": 30, "messages": 1234, "bytes": 99}}
    st.db.rows["pg_stat_activity"] = [{"version": "16.16", "connections": 12, "max_connections": 100, "slow_queries": 0}]
    st.bus.js.streams["DLQ"].state.messages = 3
    body = client.get("/api/v1/system/overview").json()
    assert body["nats"]["ok"] and body["nats"]["version"] == "2.10.29" and body["nats"]["jetstream"]["streams"] == 7
    assert body["postgres"] == {"ok": True, "version": "16.16", "connections": 12, "max_connections": 100, "slow_queries": 0}
    assert body["dlq"]["messages"] == 3 and body["go2rtc"]["ok"] and body["gateway"]["ws_clients"] == 0


def test_nats_connections_and_streams(client):
    state(client).nats_http.routes = {"/connz": {"num_connections": 1, "connections": [
        {"cid": 4, "name": "agent:telemetry", "ip": "172.18.0.9", "subscriptions": 12, "in_msgs": 5, "secret": "no"}]}}
    conns = client.get("/api/v1/system/connections").json()
    assert conns["total"] == 1 and conns["connections"][0]["name"] == "agent:telemetry"
    assert "secret" not in conns["connections"][0]
    names = [s["name"] for s in client.get("/api/v1/system/streams").json()["streams"]]
    assert names == sorted(names) and "TELEMETRY" in names


def dead_letter(seq: int, client) -> None:
    original = new_message(PausePrint(printer_id="p1", reason="x"), source="/gateway")
    dead = new_message(DeadLettered(original_subject="cmd.telemetry.pause_print", original_id=original.id,
                                    consumer="telemetry__cmd_telemetry_pause_print", error="boom", deliveries=5,
                                    original=original.model_dump(mode="json")), source="/consumers/x")
    state(client).bus.js.dlq[seq] = dead.to_bytes()
    st = state(client).bus.js.streams["DLQ"].state
    st.first_seq, st.last_seq, st.messages = 1, max(st.last_seq, seq), len(state(client).bus.js.dlq)
    return original


def test_dlq_list_replay_and_discard_are_audited(client):
    original = dead_letter(2, client)
    dead_letter(3, client)
    js, db = state(client).bus.js, state(client).db
    items = client.get("/api/v1/system/dlq").json()["items"]
    assert [i["seq"] for i in items] == [3, 2] and items[0]["error"] == "boom"

    assert client.post("/api/v1/system/dlq/2/replay").status_code == 403  # CSRF required
    r = client.post("/api/v1/system/dlq/2/replay", headers=csrf(client))
    assert r.status_code == 202 and r.json()["subject"] == "cmd.telemetry.pause_print"
    subject, env = js.published[-1]
    assert subject == "cmd.telemetry.pause_print" and env["id"] == original.id  # same message, for the inboxes
    assert js.headers[-1]["Nats-Msg-Id"] == f"{original.id}:replay:2"          # new de-dup key, not dropped
    assert 2 in js.deleted and 2 not in js.dlq

    assert client.post("/api/v1/system/dlq/3/discard", headers=csrf(client)).json() == {"discarded": True}
    assert {a["subject"] for a in db.audit.values()} == {"dlq.replay", "dlq.discard"}
    assert client.post("/api/v1/system/dlq/99/discard", headers=csrf(client)).status_code == 404


def test_settings_show_value_default_phase_and_last_change(client):
    st = state(client)
    st.bus.kv["config"] = {"safety.auto_pause": b"false"}
    st.db.rows["config_changes"] = [{"key": "safety.auto_pause", "value": False, "changed_by": "admin",
                                     "at": "2026-11-14T09:00:00+00:00"}]
    settings = {s["key"]: s for s in client.get("/api/v1/system/config").json()["settings"]}
    assert settings["safety.auto_pause"]["value"] is False and not settings["safety.auto_pause"]["is_default"]
    assert settings["safety.auto_pause"]["changed_by"] == "admin" and settings["safety.auto_pause"]["changed_value"] is False
    assert settings["inventory.auto_reorder"]["changed_by"] is None and settings["inventory.auto_reorder"]["changed_value"] is None
    assert settings["inventory.auto_reorder"]["value"] is True and settings["inventory.auto_reorder"]["is_default"]
    assert settings["fleet.quiet_hours"]["phase"] == 9


def test_system_endpoints_need_login():
    with gateway_client() as anonymous:
        for path in ("/api/v1/system/agents", "/api/v1/system/overview", "/api/v1/system/dlq", "/api/v1/printers",
                     "/api/v1/approvals"):
            assert anonymous.get(path).status_code == 401, path


# ---------------------------------------------------------------- printers, telemetry history, approvals


def test_printers_merge_fleet_live_state_and_cameras(client):
    st = state(client)
    st.db.rows["FROM fleet.printers"] = [
        {"printer_id": "p1", "vendor": "bambu", "model": "p1", "simulated": True, "bed_w": 256, "bed_d": 256,
         "nozzle_mm": 0.4, "material": "PLA", "color": "black", "online": True, "busy": False, "bed_clear": True,
         "updated_at": "2026-11-14T09:00:00Z"},
        {"printer_id": "x9", "vendor": "moonraker", "model": "klipper", "simulated": False, "bed_w": 300, "bed_d": 300,
         "nozzle_mm": 0.4, "material": "PLA", "color": "white", "online": False, "busy": False, "bed_clear": True,
         "updated_at": "2026-11-14T09:00:00Z"}]
    st.bus.kv["printer_status"] = {"p1": json.dumps({"data": {"status": "printing", "progress": 0.4}}).encode()}
    printers = {p["id"]: p for p in client.get("/api/v1/printers").json()["printers"]}
    assert printers["p1"]["state"]["status"] == "printing" and printers["p1"]["camera"]
    assert printers["p1"]["protocol"] == "Bambu MQTT" and printers["p1"]["simulated"]
    assert printers["x9"]["state"] is None and not printers["x9"]["camera"] and printers["x9"]["protocol"] == "Klipper"


def test_telemetry_history_is_downsampled_and_keeps_the_newest(client):
    js = state(client).bus.js
    js.stored["tlm.p1.state"] = [fake_msg("tlm.p1.state", "TELEMETRY", i, {"data": {
        "observed_at": f"t{i}", "status": "printing", "nozzle_temp": 200 + i, "secret_field": 1}}, pending=99 - i)
        for i in range(100)]
    body = client.get("/api/v1/printers/p1/telemetry?minutes=10&points=10").json()
    assert body["count"] == 100 and len(body["samples"]) == 10
    assert body["samples"][-1]["observed_at"] == "t99" and "secret_field" not in body["samples"][0]
    assert client.get("/api/v1/printers/Bad%20Id/telemetry").status_code == 400
    assert client.get("/api/v1/printers/p1/telemetry?minutes=120").status_code == 422  # the stream keeps one hour


def test_approvals_inbox(client):
    st = state(client)
    st.db.rows["FROM coordinator.approvals"] = [{"approval_id": "apr-1", "action": "inventory.reorder", "status": "pending"}]
    st.db.values["count(*) FROM coordinator.approvals"] = 1
    body = client.get("/api/v1/approvals").json()
    assert body["pending"] == 1 and body["approvals"][0]["approval_id"] == "apr-1"
    assert client.get("/api/v1/approvals?status=bogus").status_code == 400
