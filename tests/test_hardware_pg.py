"""Migration 022 and the fleet agent with real printer models on a real Postgres: print hours from before the
platform counted in once (and moved when corrected), maintenance last-done points, A1 tasks, and the scheduler
sending a multi-colour file to the A1 + AMS Lite and a small one to the A1 mini. Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")

A1, MINI = "pg-hw-a1", "pg-hw-mini"


def fleet_cfg(a1_hours: float = 1116, mini_hours: float = 497):
    from printfarm.config import FleetCfg, printer_cfg

    return FleetCfg(printers=[
        printer_cfg({"id": A1, "vendor": "bambu", "model": "a1", "nozzle_mm": 0.4, "ams_slots": 4,
                     "ams_filaments": ["PLA/black", "PLA/white", "PLA/red", "PLA/blue"],
                     "initial_print_hours": a1_hours, "maintenance_done_hours": {"lube-rods": 1000}}),
        printer_cfg({"id": MINI, "vendor": "bambu", "model": "a1-mini", "nozzle_mm": 0.4, "material": "PLA",
                     "color": "black", "initial_print_hours": mini_hours}),
    ], materials=[], spools=[])


@pytest.fixture
async def db():
    from printfarm.store.db import Database

    d = Database(DB_URL)
    await d.open()
    await d.migrate()
    async with d.tx() as conn:
        for t in ("fleet.plate_items", "fleet.jobs", "fleet.plates", "fleet.maintenance_log", "fleet.maintenance_tasks"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.execute("UPDATE fleet.printers SET online = false")       # other tests' printers stay out of it
        await conn.execute("DELETE FROM fleet.printers WHERE printer_id = ANY(%s)", ([A1, MINI],))
        await conn.execute("DELETE FROM messaging.outbox WHERE producer = 'fleet'")
    yield d
    await d.close()


async def start(db, fleet):
    from printfarm.agents.fleet.agent import FleetAgent
    from printfarm.config import Settings

    a = FleetAgent(None, db, Settings(database_url=DB_URL), fleet)
    a.spawn = lambda coro: coro.close()
    await a.on_start()
    return a


async def rows(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def test_print_hours_from_before_count_once_and_move_when_corrected(db):
    await start(db, fleet_cfg())
    await start(db, fleet_cfg())                                     # a restart does not count them twice
    p = {r["printer_id"]: r for r in await rows(db, "SELECT * FROM fleet.printers WHERE printer_id = ANY(%s)", [A1, MINI])}
    assert p[A1]["print_seconds_total"] == 1116 * 3600 and p[MINI]["print_seconds_total"] == 497 * 3600
    assert p[A1]["ams_slots"] == 4 and p[A1]["max_z"] == 256 and p[MINI]["max_z"] == 180
    assert p[A1]["loaded"][2] == ["PLA", "red"] and p[MINI]["loaded"] == []
    tasks = {r["task_id"]: r for r in await rows(db, "SELECT * FROM fleet.maintenance_tasks WHERE printer_id = %s", A1)}
    assert {"hotend-wear", "cutter-ptfe", "bed-cable", "lube-rods", "nozzle-check"} <= set(tasks)
    assert tasks["lube-rods"]["last_done_seconds"] == 1000 * 3600       # fleet.toml: last lubricated at 1000 h
    assert tasks["nozzle-check"]["last_done_seconds"] == 1116 * 3600    # unknown: counted from onboarding

    await start(db, fleet_cfg(a1_hours=1120))                       # the owner corrected the number
    p = (await rows(db, "SELECT print_seconds_total, seed_hours FROM fleet.printers WHERE printer_id = %s", A1))[0]
    assert p["print_seconds_total"] == 1120 * 3600 and p["seed_hours"] == 1120
    t = (await rows(db, "SELECT last_done_seconds FROM fleet.maintenance_tasks WHERE printer_id = %s "
                        "AND task_id = 'nozzle-check'", A1))[0]
    assert t["last_done_seconds"] == 1120 * 3600                     # still "done at onboarding", not overdue


async def test_a_multi_colour_file_prints_on_the_a1_and_a_small_one_on_the_mini(db):
    from printfarm.contracts import new_message
    from printfarm.contracts.messages import EnqueueJob

    a = await start(db, fleet_cfg())
    async with db.tx() as conn:
        await conn.execute("UPDATE fleet.printers SET online = true WHERE printer_id = ANY(%s)", ([A1, MINI],))

    async def enqueue(jid, **kw):
        cmd = EnqueueJob(job_id=jid, order_id="o-" + jid, artifact_uri=f"file:///{jid}.gcode.3mf", material="PLA",
                         color="black", footprint_mm=(100, 100), est_grams=20, est_minutes=60, whole_plate=True, **kw)
        async with db.tx() as conn:
            await a.enqueue(new_message(cmd, source="/test"), cmd, conn)

    await enqueue("multi", filament_count=3)
    await enqueue("small", printer_model="N1")                         # sliced for the A1 mini
    assert await a.schedule() == 2
    placed = {r["job_id"]: r["printer_id"] for r in await rows(
        db, "SELECT j.job_id, p.printer_id FROM fleet.jobs j JOIN fleet.plates p USING (plate_id)")}
    assert placed == {"multi": A1, "small": MINI}
    job = (await rows(db, "SELECT filament_count, printer_model FROM fleet.jobs WHERE job_id = 'small'"))[0]
    assert job["printer_model"] == "N1" and job["filament_count"] == 1
