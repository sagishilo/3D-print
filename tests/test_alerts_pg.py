"""Migration 021 and alert delivery on a real Postgres: with no channel on, notifications stay on the dashboard; with
Telegram and e-mail on (test mode) each channel sends each notification once; quiet hours hold a warning until they
end while a critical safety alert goes at once; a channel below its minimum severity skips; a failing channel is
retried without sending twice on the other, then given up; a test message goes to one channel only; a notification
older than a day is dropped. Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")

DAY = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)          # 14:00 in Israel
NIGHT = datetime(2026, 11, 20, 21, 30, tzinfo=UTC)       # 23:30 in Israel: quiet hours


@pytest.fixture
async def agent():
    from printfarm.agents.integrations.agent import IntegrationsAgent
    from printfarm.config import Settings
    from printfarm.store.db import Database

    db = Database(DB_URL)
    await db.open()
    await db.migrate()
    async with db.tx() as conn:
        for t in ("integrations.documents", "integrations.shipments", "integrations.pushes", "integrations.store_orders",
                  "integrations.sku_map", "integrations.runs", "integrations.mappings", "integrations.sync_state",
                  "integrations.connectors", "notify.outbox"):
            await conn.execute(f"DELETE FROM {t}")
    a = IntegrationsAgent(None, db, Settings(database_url=DB_URL), None)
    a.spawn = lambda coro: coro.close()
    await a.on_start()
    yield a, db
    await db.close()


async def rows(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def note(a, db, severity="warning", topic="maintenance", key=None, created=None):
    from printfarm.agents.integrations import alerts

    async with db.tx() as conn:
        nid = await a.queue(conn, alerts.Note(topic, severity, f"{topic} {severity}", "body", "/today", key))
        if created:
            await conn.execute("UPDATE notify.outbox SET created_at = %s, next_at = %s WHERE id = %s", (created, created, nid))
    return nid


async def enable(db, cid, settings="{}"):
    async with db.tx() as conn:
        await conn.execute("UPDATE integrations.connectors SET enabled = true, settings = %s::jsonb WHERE connector_id = %s",
                           (settings, cid))


async def outbox(db):
    return {r["id"]: r for r in await rows(db, "SELECT id, status, channels, delivery, attempts, next_at, error FROM notify.outbox")}


async def test_no_channel_keeps_it_on_the_dashboard_and_each_channel_sends_once(agent):
    a, db = agent
    first = await note(a, db, created=DAY)
    assert (await outbox(db))[first]["status"] == "dashboard"
    assert await a.deliver_due(DAY) == 0
    await enable(db, "telegram")
    await enable(db, "email")
    n = await note(a, db, created=DAY)
    assert await a.deliver_due(DAY) == 2
    assert await a.deliver_due(DAY + timedelta(minutes=1)) == 0                     # sent: never again
    o = (await outbox(db))[n]
    assert (o["status"], o["channels"], o["delivery"]) == ("sent", ["telegram (test)", "email (test)"],
                                                           {"telegram": "sent", "email": "sent"})
    assert (await outbox(db))[first]["status"] == "dashboard"                       # older ones are not flooded out


async def test_quiet_hours_hold_warnings_but_not_safety(agent):
    a, db = agent
    await enable(db, "telegram")
    warn = await note(a, db, "warning", created=NIGHT)
    crit = await note(a, db, "critical", "safety", created=NIGHT)
    await a.deliver_due(NIGHT)
    o = await outbox(db)
    assert o[crit]["status"] == "sent"
    assert o[warn]["status"] == "held" and o[warn]["next_at"] == datetime(2026, 11, 21, 5, 0, tzinfo=UTC)   # 07:00 IL
    assert await a.deliver_due(NIGHT + timedelta(hours=2)) == 0
    assert await a.deliver_due(datetime(2026, 11, 21, 5, 0, tzinfo=UTC)) == 1
    assert (await outbox(db))[warn]["status"] == "sent"


async def test_minimum_severity_skips_and_a_failing_channel_retries_without_resending_the_other(agent):
    a, db = agent
    await enable(db, "telegram", '{"min_severity": "critical"}')
    await enable(db, "email", '{"fail": true}')
    n = await note(a, db, "warning", created=DAY)
    for i in range(5):
        await a.deliver_due(DAY + timedelta(hours=i))
    o = (await outbox(db))[n]
    assert o["delivery"] == {"telegram": "skipped", "email": "failed"} and o["status"] == "failed" and o["attempts"] == 5
    assert "set to fail" in o["error"]


async def test_a_test_message_goes_to_one_channel_and_old_ones_are_dropped(agent):
    from printfarm.contracts import new_message
    from printfarm.contracts.messages import SendTestAlert

    a, db = agent
    await enable(db, "telegram")
    await enable(db, "email")
    msg = SendTestAlert(connector_id="telegram", requested_by="owner")
    async with db.tx() as conn:
        await a.test_alert(new_message(msg, source="/t"), msg, conn)
    old = await note(a, db, created=DAY - timedelta(days=2))
    await a.deliver_due(datetime.now(UTC))
    o = await outbox(db)
    test = next(r for r in o.values() if r["id"] != old)
    assert test["delivery"] == {"telegram": "sent", "email": "skipped"} and test["channels"] == ["telegram (test)"]
    assert o[old]["status"] == "skipped" and o[old]["error"] == "too old to send"
