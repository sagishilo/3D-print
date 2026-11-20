"""Migration 017 and the integrations agent on a real Postgres: the demo connector seeded off and in fake mode,
syncs that map outside records once (a repeat creates nothing), failures that back off and, at the third in a row,
tell an owner once; the outbox never queues the same open problem twice. Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")


@pytest.fixture
async def agent():
    from printfarm.agents.integrations.agent import IntegrationsAgent
    from printfarm.config import Settings
    from printfarm.store.db import Database

    db = Database(DB_URL)
    await db.open()
    await db.migrate()
    async with db.tx() as conn:
        for t in ("integrations.runs", "integrations.mappings", "integrations.sync_state", "integrations.connectors",
                  "notify.outbox"):
            await conn.execute(f"DELETE FROM {t}")
    a = IntegrationsAgent(None, db, Settings(database_url=DB_URL), None)
    a.spawn = lambda coro: coro.close()                 # no background scheduler in the test: we drive it
    await a.on_start()
    yield a, db
    await db.close()


async def one(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchone()


async def test_a_connector_starts_off_in_fake_mode_and_maps_once(agent):
    a, db = agent
    c = await one(db, "SELECT enabled, mode, secrets_status FROM integrations.connectors WHERE connector_id = 'demo'")
    assert c == {"enabled": False, "mode": "fake", "secrets_status": {"DEMO_STORE_TOKEN": False}}
    assert await a.tick(datetime.now(UTC)) == []                      # off: never runs
    async with db.tx() as conn:
        await conn.execute("UPDATE integrations.connectors SET enabled = true WHERE connector_id = 'demo'")
    first = await a.run("demo", trigger="manual")
    second = await a.run("demo", trigger="manual")
    assert (first.ok, first.created) == (True, 1) and (second.created, second.updated, second.items) == (1, 0, 2)
    n = await one(db, "SELECT count(*) AS n FROM integrations.mappings WHERE connector_id = 'demo'")
    assert n["n"] == 2
    state = await one(db, "SELECT runs, consecutive_failures, cursor FROM integrations.sync_state WHERE connector_id = 'demo'")
    assert state == {"runs": 2, "consecutive_failures": 0, "cursor": {"seen": 2, "runs": 2}}


async def test_failures_back_off_and_the_third_tells_an_owner_once(agent):
    a, db = agent
    from psycopg.types.json import Jsonb

    async with db.tx() as conn:
        await conn.execute("UPDATE integrations.connectors SET enabled = true, settings = %s WHERE connector_id = 'demo'",
                           (Jsonb({"fail": True}),))
    results = [await a.run("demo", trigger="schedule") for _ in range(4)]
    assert [r.failures for r in results] == [1, 2, 3, 4] and not any(r.ok for r in results)
    assert results[1].next_run_s > results[0].next_run_s                # each failure waits longer
    notes = await one(db, "SELECT count(*) AS n, min(min_role) AS role FROM notify.outbox WHERE topic = 'integration'")
    assert notes == {"n": 1, "role": "owner"}
    run = await one(db, "SELECT ok, error FROM integrations.runs WHERE connector_id = 'demo' ORDER BY id DESC LIMIT 1")
    assert run["ok"] is False and "set to fail" in run["error"]


async def test_live_mode_refuses_without_its_secret(agent):
    a, db = agent
    async with db.tx() as conn:
        await conn.execute("UPDATE integrations.connectors SET enabled = true, mode = 'live' WHERE connector_id = 'demo'")
    r = await a.run("demo", trigger="manual")
    assert not r.ok and "DEMO_STORE_TOKEN" in r.error


async def test_the_outbox_queues_an_open_problem_once(agent):
    a, db = agent
    from printfarm.agents.integrations.alerts import Note

    async with db.tx() as conn:
        first = await a.queue(conn, Note("safety", "critical", "x1 did not pause", dedupe_key="safety:x1:p1"))
        again = await a.queue(conn, Note("safety", "critical", "x1 did not pause", dedupe_key="safety:x1:p1"))
        other = await a.queue(conn, Note("stock", "warning", "short", dedupe_key=None))
    assert first and again is None and other
    row = await one(db, "SELECT status, severity FROM notify.outbox WHERE id = %s", first)
    assert row == {"status": "dashboard", "severity": "critical"}
