"""Phase 11 hardening: NATS permissions, the agents' command guard, migration bookkeeping, and the gateway's
rate limits, body limits, outage answers, health and auth audit."""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from printfarm.agents.base import Agent
from printfarm.contracts import REGISTRY, Kind
from printfarm.contracts.messages import DecideApproval, EnqueueJob
from printfarm.contracts.permissions import AGENT_COMMANDS, AGENT_DENY, AGENT_PUBLISH, agent_may_send
from printfarm.store import schema

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- NATS permissions


def _agents_block() -> str:
    conf = (ROOT / "infra/nats/nats.conf").read_text(encoding="utf-8")
    start = conf.index('user: "agents"')
    return conf[start:conf.index("subscribe:", start)]


def _list(block: str, key: str) -> set[str]:
    body = re.search(key + r":\s*\[(.*?)\]", block, re.S)
    assert body, f"no {key} list for the agents user"
    return set(re.findall(r'"([^"]+)"', body.group(1)))


def test_nats_conf_matches_the_contract():
    block = _agents_block()
    assert _list(block, "allow") == set(AGENT_PUBLISH) | AGENT_COMMANDS
    assert _list(block, "deny") == set(AGENT_DENY)
    assert "cmd.>" not in _list(block, "allow")


def test_agent_commands_are_real_commands_and_operator_decisions_are_not_among_them():
    commands = {m.subject for m in REGISTRY.values() if m.kind == Kind.COMMAND}
    assert commands >= AGENT_COMMANDS
    for operator_only in ("cmd.coordinator.decide_approval", "cmd.coordinator.record_payment", "cmd.coordinator.set_config",
                          "cmd.telemetry.cancel_print", "cmd.telemetry.pause_all", "cmd.inventory.confirm_po_ordered",
                          "cmd.finance.create_expense", "cmd.personalization.upsert_product"):
        assert operator_only in commands and not agent_may_send(operator_only)
    assert agent_may_send("evt.fleet.job.queued") and agent_may_send("tlm.bambu-01.state")


def test_cli_uses_the_services_user():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    cli = compose[compose.index("\n  cli:"):]
    assert "nats://services:" in cli.split("\nvolumes:")[0]


async def test_agent_emit_refuses_operator_commands(monkeypatch):
    import printfarm.agents.base as base

    added = []

    async def outbox_add(conn, producer, env):
        added.append(env)

    monkeypatch.setattr(base, "outbox_add", outbox_add)
    fake = SimpleNamespace(source="/agents/coordinator", name="coordinator", log=SimpleNamespace(info=lambda *a: None))
    with pytest.raises(PermissionError, match="operator command"):
        await Agent.emit(fake, None, DecideApproval(approval_id="a-1", approved=True, decided_by="saga"))
    assert added == []
    job = EnqueueJob(job_id="j-1", order_id="o-1", artifact_uri="file:///x.3mf", material="PLA", color="black",
                     footprint_mm=(40, 20), est_grams=10, est_minutes=30)
    await Agent.emit(fake, None, job)                                   # an agent-to-agent command passes
    assert [e.nats_subject() for e in added] == ["cmd.fleet.enqueue_job"]


# ---------------------------------------------------------------- migrations


def _m(name: str, sql: str = "SELECT 1") -> schema.Migration:
    return schema.Migration(name, sql)


def test_migration_plan_pending_adopt_changed_unknown():
    files = [_m("001_a.sql"), _m("002_b.sql", "CREATE TABLE b()"), _m("003_c.sql")]
    p = schema.plan(files, {"001_a.sql": files[0].checksum, "002_b.sql": None})
    assert [m.name for m in p.pending] == ["003_c.sql"] and [m.name for m in p.adopt] == ["002_b.sql"] and p.ok
    assert p.latest == "002_b.sql" and p.summary()["pending"] == ["003_c.sql"]

    p = schema.plan(files, {"001_a.sql": "0" * 64, "000_gone.sql": "1" * 64})
    assert p.changed == ["001_a.sql"] and p.unknown == ["000_gone.sql"] and not p.ok
    assert any("changed after it was applied" in x for x in p.problems)
    assert any("newer than the code" in x for x in p.problems)


def test_line_endings_do_not_count_as_a_change():
    assert schema.checksum("A;\r\nB;\r\n") == schema.checksum("A;\nB;\n")
    assert schema.checksum("A;\n") != schema.checksum("B;\n")


def test_bundled_migrations_are_numbered_and_unique():
    names = [m.name for m in schema.bundled()]
    assert names and names == sorted(names)
    assert all(re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", n) for n in names)
    assert len({n[:3] for n in names}) == len(names)          # no two files share a number


# ---------------------------------------------------------------- gateway

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
import gateway_support as gw  # noqa: E402 - sets the gateway's environment and sys.path


@pytest.fixture
def client():
    with gw.gateway_client() as c:
        yield c


def test_rate_limiter_window():
    from app.middleware import RateLimiter, bucket_for

    rl = RateLimiter({"commands": 2})
    assert rl.check("1.2.3.4", "commands", now=0) == 0 and rl.check("1.2.3.4", "commands", now=1) == 0
    assert rl.check("1.2.3.4", "commands", now=2) == pytest.approx(58)
    assert rl.check("5.6.7.8", "commands", now=2) == 0                 # per client
    assert rl.check("1.2.3.4", "commands", now=60.5) == 0              # the window slides
    assert rl.check("1.2.3.4", "api", now=0) == 0                      # no limit set
    assert bucket_for("POST", "/api/v1/commands/orders.create") == "commands"
    assert bucket_for("POST", "/api/v1/catalog/uploads") == "uploads"
    assert bucket_for("GET", "/api/v1/orders") == "api"
    assert bucket_for("GET", "/health") is None


def test_commands_are_rate_limited_with_retry_after(client):
    gw.login(client)
    gw.gateway.limiter.limits["commands"] = 3
    try:
        rs = [client.post("/api/v1/commands/printers.pause_all", json={}, headers=gw.csrf(client)) for _ in range(4)]
        assert client.get("/api/v1/auth/me").status_code == 200       # other buckets are unaffected
    finally:
        gw.gateway.limiter.limits["commands"] = gw.gateway.settings.rate_commands_per_min
    assert [r.status_code for r in rs] == [202, 202, 202, 429]
    assert int(rs[3].headers["retry-after"]) >= 1 and "request_id" in rs[3].json()


def test_request_id_is_echoed_or_made(client):
    r = client.get("/api/v1/auth/me", headers={"x-request-id": "abc-12345678"})
    assert r.status_code == 401 and r.headers["x-request-id"] == "abc-12345678"
    r = client.get("/api/v1/auth/me", headers={"x-request-id": "bad id with spaces"})
    assert re.fullmatch(r"[0-9a-f]{16}", r.headers["x-request-id"])


def test_oversized_bodies_are_413(client):
    gw.login(client)
    big = b'{"x": "' + b"a" * (gw.gateway.settings.max_body_bytes + 10) + b'"}'
    r = client.post("/api/v1/commands/orders.create", content=big,
                    headers={**gw.csrf(client), "content-type": "application/json"})
    assert r.status_code == 413

    def chunks():                                                      # no Content-Length: counted as it arrives
        for _ in range(20):
            yield b"a" * 100_000

    r = client.post("/api/v1/commands/orders.create", content=chunks(), headers=gw.csrf(client))
    assert r.status_code == 413


def test_database_outage_is_a_503_not_a_500(client):
    from psycopg import OperationalError

    gw.login(client)

    async def down(*a, **kw):
        raise OperationalError("connection refused")

    client.app.state.db.fetchall = down
    client.app.state.db.fetchrow = down
    r = client.get("/api/v1/orders")
    assert r.status_code == 503 and r.headers["retry-after"] == "5" and "database unavailable" in r.json()["detail"]


def test_health_reports_the_schema(client):
    db = client.app.state.db
    files = schema.bundled()
    db.rows["schema_migrations"] = [{"name": m.name, "checksum": m.checksum} for m in files[:-1]]
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["migrations"]["pending"] == [files[-1].name]
    db.rows["schema_migrations"].append({"name": files[-1].name, "checksum": "0" * 64})
    r = client.get("/health")
    assert r.status_code == 503 and "changed after it was applied" in r.json()["migrations"]["problems"][0]
    db.rows["schema_migrations"][-1]["checksum"] = files[-1].checksum
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["migrations"]["ok"] and r.json()["migrations"]["latest"] == files[-1].name


def test_sign_ins_failures_and_sign_outs_are_audited(client):
    gw.login(client, "wrong")
    gw.login(client)
    client.post("/api/v1/auth/logout")
    events = [(a["subject"], a["outcome"]) for a in client.app.state.db.audit.values()]
    assert events == [("auth.login", "failed"), ("auth.login", "done"), ("auth.logout", "done")]
    failed = next(iter(client.app.state.db.audit.values()))
    assert failed["payload"]["attempt"] == 1 and "ip" in failed["payload"] and "password" not in str(failed["payload"])


def test_api_docs_are_not_served(client):
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


# ---------------------------------------------------------------- live NATS (needs the server running nats.conf)

AGENTS_URL, SERVICES_URL = os.getenv("TEST_NATS_AGENTS_URL"), os.getenv("TEST_NATS_URL")


@pytest.mark.skipif(not (AGENTS_URL and SERVICES_URL), reason="needs TEST_NATS_URL and TEST_NATS_AGENTS_URL (nats.conf)")
async def test_the_agents_user_cannot_send_operator_commands_or_delete_streams():
    import nats
    from nats.errors import TimeoutError as NatsTimeout

    from printfarm.bus.nats_bus import NatsBus
    from printfarm.contracts import new_message

    errors: list[Exception] = []

    async def on_error(exc):
        errors.append(exc)

    svc = await NatsBus.connect(SERVICES_URL, name="test-services")                 # ensures the streams exist
    agent = await nats.connect(AGENTS_URL, name="test-agents", error_cb=on_error)
    js = agent.jetstream()
    try:
        before = (await svc.js.stream_info("COMMANDS")).state.last_seq
        env = new_message(DecideApproval(approval_id="a-x", approved=True, decided_by="intruder"), source="/test")
        with pytest.raises(NatsTimeout):
            await js.publish(env.nats_subject(), env.to_bytes(), timeout=1)
        assert (await svc.js.stream_info("COMMANDS")).state.last_seq == before    # nothing stored
        with pytest.raises(NatsTimeout):
            await js.delete_stream("DLQ")
        assert (await svc.js.stream_info("DLQ")).config.name == "DLQ"
        ok = new_message(EnqueueJob(job_id="j-perm", order_id="o-perm", artifact_uri="file:///x.3mf", material="PLA",
                                    color="black", footprint_mm=(40, 20), est_grams=1, est_minutes=1), source="/test")
        ack = await js.publish(ok.nats_subject(), ok.to_bytes(), timeout=2)                # agent-to-agent: allowed
        assert ack.stream == "COMMANDS"
        assert any("permissions violation" in str(e).lower() for e in errors)
    finally:
        await agent.close()
        await svc.close()


# ---------------------------------------------------------------- prints that ended unseen


def _st(status: str, plate: str | None = None, progress: float = 0.0):
    from datetime import UTC, datetime

    from printfarm.contracts.messages import PrinterState

    return PrinterState(printer_id="bambu-01", vendor="bambu", status=status, plate_id=plate, progress=progress,
                        observed_at=datetime.now(UTC))


def test_a_print_is_lost_only_after_the_printer_contradicts_it_for_a_while():
    from printfarm.agents.telemetry.lost import LostPrintWatch

    w = LostPrintWatch(after_s=20)
    w.opened("bambu-01", "pl-1")
    assert w.check("bambu-01", _st("printing", "pl-1", 0.4), now=0) == []         # still printing it
    assert w.check("bambu-01", _st("idle"), now=10) == []                          # suspicious, not yet
    assert w.check("bambu-01", _st("printing", "pl-1", 0.5), now=15) == []        # it was a blip: forgotten
    assert w.check("bambu-01", _st("idle"), now=40) == []                          # the 20 s start again here
    assert w.check("bambu-01", _st("offline"), now=55) == []                       # offline proves nothing
    assert w.check("bambu-01", _st("idle"), now=60) == ["pl-1"]                    # 20 s of idle: lost
    assert w.check("bambu-01", _st("idle"), now=90) == []                          # reported once only


def test_printing_another_plate_or_a_normal_finish():
    from printfarm.agents.telemetry.lost import LostPrintWatch

    w = LostPrintWatch(after_s=0)
    w.opened("bambu-01", "pl-1")
    w.opened("bambu-01", "pl-2")
    assert w.check("bambu-01", _st("finished", "pl-2", 1.0), now=0) == ["pl-1"]   # pl-2's own end is the normal path
    w.closed("bambu-01", "pl-2")
    w.opened("bambu-01", "pl-3")
    assert w.check("bambu-01", _st("calibrating"), now=1) == []
    assert w.check("bambu-01", _st("printing", "pl-9"), now=2) == ["pl-3"]         # someone started another job
    assert w.check("voron-01", _st("idle"), now=3) == []                           # other printers untouched


def test_a_lost_plate_never_marks_the_bed_clear():
    from printfarm.contracts.messages import PrintFinished

    ev = PrintFinished(printer_id="bambu-01", plate_id="pl-1", succeeded=False, progress=0.0, reason="print lost",
                       bed_state_unknown=True)
    auto_clear_bed = False
    assert (auto_clear_bed or (not ev.succeeded and ev.progress == 0 and not ev.bed_state_unknown)) is False
    src = (ROOT / "src/printfarm/agents/fleet/agent.py").read_text(encoding="utf-8")
    assert "not ev.bed_state_unknown" in src
