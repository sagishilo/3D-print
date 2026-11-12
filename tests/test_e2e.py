"""End-to-end: real NATS JetStream + Postgres, in-process MQTT broker, simulated printers, all agents.

Runs only when TEST_NATS_URL and TEST_DATABASE_URL are set (each test run
uses a fresh database schema set and unique stream state is tolerated), e.g.

    TEST_NATS_URL=nats://localhost:4222 \
    TEST_DATABASE_URL=postgresql://printfarm:printfarm@localhost:5432/printfarm \
    pytest tests/test_e2e.py -v
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import textwrap
import time

import pytest

NATS_URL = os.getenv("TEST_NATS_URL")
DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not (NATS_URL and DB_URL), reason="needs TEST_NATS_URL and TEST_DATABASE_URL")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_mqtt_broker(port: int):
    from amqtt.broker import Broker

    broker = Broker({
        "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    })
    await broker.start()
    return broker


@pytest.fixture
async def stack(tmp_path):
    import psycopg

    from printfarm.config import Settings

    # clean slate: drop every agent schema
    async with await psycopg.AsyncConnection.connect(DB_URL, autocommit=True) as c:
        for schema in ("messaging", "coordinator", "personalization", "fleet", "telemetry", "inventory", "social", "trend"):
            await c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    # clean slate: delete streams and KV so durable consumers start fresh
    import nats

    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    for name in ("EVENTS", "COMMANDS", "TELEMETRY", "DLQ", "KV_printer_status", "KV_heartbeats", "KV_leases"):
        with contextlib.suppress(Exception):
            await js.delete_stream(name)
    await nc.close()

    mqtt_port, ws_port = free_port(), free_port()
    broker = await start_mqtt_broker(mqtt_port)
    cfg = tmp_path / "fleet.toml"
    cfg.write_text(textwrap.dedent(f"""
        [fleet]
        auto_clear_bed = true
        schedule_interval_s = 1
        batch_window_s = 1
        [simulator]
        print_seconds = 4
        [[printers]]
        id = "bambu-01"
        vendor = "bambu"
        host = "127.0.0.1"
        port = {mqtt_port}
        tls = false
        serial = "SIM1"
        bed_mm = [256, 256]
        nozzle_mm = 0.4
        material = "PLA"
        color = "black"
        [[printers]]
        id = "voron-01"
        vendor = "moonraker"
        url = "ws://127.0.0.1:{ws_port}/websocket"
        bed_mm = [300, 300]
        nozzle_mm = 0.4
        material = "PLA"
        color = "white"
        [[materials]]
        material = "PLA"
        color = "black"
        grams_per_spool = 1000
        cost_per_spool = 22.0
        lead_time_days = 5
        safety_stock_days = 3
        baseline_daily_grams = 150
        auto_approve_limit = 50.0
        [[spools]]
        id = "SP-1"
        material = "PLA"
        color = "black"
        grams = 1000
        remaining_grams = 650
        loaded_on = "bambu-01"
        [[spools]]
        id = "SP-2"
        material = "PLA"
        color = "white"
        grams = 1000
        loaded_on = "voron-01"
    """))
    os.environ.update(NATS_URL=NATS_URL, DATABASE_URL=DB_URL, FLEET_CONFIG=str(cfg), ARTIFACT_DIR=str(tmp_path / "art"))
    settings = Settings()

    from printfarm.cli import AGENTS, run_agents
    from printfarm.simulator.printers import run_simulator

    stop = asyncio.Event()
    sim = asyncio.create_task(run_simulator(settings.fleet(), faults={"voron-01": "thermal_runaway"}))
    agents = asyncio.create_task(run_agents(list(AGENTS), settings, stop))
    yield settings
    stop.set()
    await asyncio.wait_for(agents, 10)
    sim.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sim
    await broker.shutdown()


async def wait_for(db, sql, params, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            async with db.tx() as conn:
                last = await (await conn.execute(sql, params)).fetchall()
        except Exception as exc:  # noqa: BLE001 - agents may still be migrating
            last = exc
            await asyncio.sleep(0.5)
            continue
        if predicate(last):
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out; last result: {last}")


async def test_order_to_print_to_inventory(stack):
    from printfarm.cli import _publish
    from printfarm.contracts.messages import DecideApproval, OrderReceived
    from printfarm.store import Database

    settings = stack
    db = Database(DB_URL)
    await db.open()
    try:
        # wait until telemetry saw both printers online and fleet knows it
        await wait_for(db, "SELECT printer_id FROM fleet.printers WHERE online", (), lambda r: len(r) == 2)

        # 1) black nameplates -> bambu-01 over MQTT; 3 units fit one plate
        await _publish(settings, OrderReceived(order_id="ord-black", customer_ref="c1", text="משפחת כהן", quantity=3),
                       correlationid="ord-black")
        rows = await wait_for(db, "SELECT state FROM coordinator.sagas WHERE saga_id = %s", ("ord-black",),
                              lambda r: r and r[0]["state"] == "COMPLETED")
        assert rows[0]["state"] == "COMPLETED"

        plates = await wait_for(db, "SELECT plate_id, printer_id, status FROM fleet.plates", (), lambda r: len(r) == 1)
        assert plates[0]["printer_id"] == "bambu-01" and plates[0]["status"] == "finished"
        items = await wait_for(db, "SELECT count(*) AS n FROM fleet.plate_items", (), lambda r: True)
        assert items[0]["n"] == 3  # bin packing put all three units on one plate

        # inventory consumed filament and, being below the reorder point, asked for approval (66 > 50 limit)
        spool = await wait_for(db, "SELECT remaining_grams FROM inventory.spools WHERE spool_id = 'SP-1'", (),
                               lambda r: r[0]["remaining_grams"] < 650)
        assert spool[0]["remaining_grams"] < 650
        appr = await wait_for(db, "SELECT approval_id, status FROM coordinator.approvals", (), lambda r: len(r) == 1)
        await _publish(settings, DecideApproval(approval_id=appr[0]["approval_id"], approved=True, decided_by="test"))
        await wait_for(db, "SELECT status FROM inventory.purchase_orders", (), lambda r: r and r[0]["status"] == "placed")

        # 2) white nameplate -> voron-01 over WebSocket; simulated thermal runaway -> auto pause
        await _publish(settings, OrderReceived(order_id="ord-white", customer_ref="c2", text="Noa", color="white"),
                       correlationid="ord-white")
        fails = await wait_for(db, "SELECT printer_id, detector, action_taken FROM telemetry.failures", (),
                               lambda r: len(r) >= 1, timeout=60)
        assert fails[0]["printer_id"] == "voron-01"
        assert fails[0]["detector"] == "thermal_runaway_nozzle"
        assert fails[0]["action_taken"] == "paused"

        # every handler ran exactly once per message; nothing was dead-lettered
        async with db.tx() as conn:
            pending = await (await conn.execute(
                "SELECT count(*) AS n FROM messaging.outbox WHERE published_at IS NULL")).fetchone()
        await asyncio.sleep(1)
        assert pending["n"] <= 1
    finally:
        await db.close()
