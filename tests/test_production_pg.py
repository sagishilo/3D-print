"""Migration 018 and the fleet agent's production features on a real Postgres: default maintenance tasks seeded once,
the queue's manual order / hold / pin driving what gets scheduled, auto plate-swap clearing the bed until the stack
is empty, counters, and maintenance that blocks a printer until it is done. Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")

P = "pg-p1"


@pytest.fixture
async def agent():
    from printfarm.agents.fleet.agent import FleetAgent
    from printfarm.config import FleetCfg, PrinterCfg, Settings
    from printfarm.store.db import Database

    db = Database(DB_URL)
    await db.open()
    await db.migrate()
    async with db.tx() as conn:
        for t in ("fleet.plate_items", "fleet.jobs", "fleet.plates", "fleet.maintenance_log", "fleet.maintenance_tasks"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.execute("DELETE FROM fleet.printers WHERE printer_id = %s", (P,))
        await conn.execute("DELETE FROM messaging.outbox WHERE producer = 'fleet'")
    fleet = FleetCfg(printers=[PrinterCfg(id=P, vendor="bambu", bed_mm=(256, 256), nozzle_mm=0.4, material="PLA",
                                          color="black")], materials=[], spools=[])
    a = FleetAgent(None, db, Settings(database_url=DB_URL), fleet)
    a.spawn = lambda coro: coro.close()                 # no background scheduler: the test drives it
    await a.on_start()
    async with db.tx() as conn:
        await conn.execute("UPDATE fleet.printers SET online = true WHERE printer_id = %s", (P,))
    yield a, db
    await db.close()


async def rows(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def send(a, db, handler, payload):
    from printfarm.contracts import new_message

    env = new_message(payload, source="/test")
    async with db.tx() as conn:
        await handler(env, payload, conn)


async def enqueue(a, db, jid, **kw):
    from printfarm.contracts.messages import EnqueueJob

    await send(a, db, a.enqueue, EnqueueJob(job_id=jid, order_id="o-" + jid, artifact_uri=f"file:///{jid}",
                                            material="PLA", color="black", footprint_mm=(200, 200), est_grams=20,
                                            est_minutes=60, **kw))


async def finish(a, db, plate_id, ok=True):
    from printfarm.contracts.messages import PrintFinished

    await send(a, db, a.print_finished, PrintFinished(printer_id=P, plate_id=plate_id, succeeded=ok,
                                                      progress=1.0 if ok else 0.5, reason="" if ok else "spaghetti"))


async def plates(db):
    return await rows(db, "SELECT p.plate_id, array_agg(j.job_id ORDER BY j.job_id) AS jobs FROM fleet.plates p "
                          "JOIN fleet.jobs j USING (plate_id) GROUP BY p.plate_id, p.created_at ORDER BY p.created_at")


async def test_default_maintenance_is_seeded_once(agent):
    a, db = agent
    await a.on_start()
    tasks = await rows(db, "SELECT task_id FROM fleet.maintenance_tasks WHERE printer_id = %s ORDER BY task_id", P)
    assert [t["task_id"] for t in tasks] == ["clean-plate", "lube-rods", "nozzle-check"]


async def test_manual_order_hold_and_pins_decide_what_prints(agent):
    from printfarm.contracts.messages import ControlJob, MarkBedCleared

    a, db = agent
    for jid in ("a", "b", "c"):
        await enqueue(a, db, jid)
    await send(a, db, a.control_job, ControlJob(job_id="c", action="top", changed_by="t"))
    await send(a, db, a.control_job, ControlJob(job_id="a", action="hold", changed_by="t"))
    await send(a, db, a.control_job, ControlJob(job_id="b", action="pin", printer_id="nope", changed_by="t"))  # ignored
    ranks = {r["job_id"]: (r["rank"], r["hold"], r["pinned_printer"])
             for r in await rows(db, "SELECT job_id, rank, hold, pinned_printer FROM fleet.jobs")}
    assert ranks == {"a": (None, True, None), "b": (None, False, None), "c": (1, False, None)}
    assert await a.schedule() == 1
    assert [p["jobs"] for p in await plates(db)] == [["c"]]           # one 200 mm part per bed, the manual one first
    first = (await plates(db))[0]["plate_id"]
    sched = await rows(db, "SELECT rank FROM fleet.jobs WHERE job_id = 'c'")
    assert sched[0]["rank"] is None                                   # a scheduled job leaves the manual order
    await finish(a, db, first)
    await send(a, db, a.bed_cleared, MarkBedCleared(printer_id=P, operator="t"))
    assert await a.schedule() == 1
    assert [p["jobs"] for p in await plates(db)][-1] == ["b"]         # "a" is held
    await send(a, db, a.control_job, ControlJob(job_id="a", action="release", changed_by="t"))
    assert (await rows(db, "SELECT hold FROM fleet.jobs WHERE job_id = 'a'"))[0]["hold"] is False


async def test_auto_eject_clears_the_bed_until_the_stack_is_empty(agent):
    from printfarm.contracts.messages import SetPrinterPolicy

    a, db = agent
    await send(a, db, a.printer_policy, SetPrinterPolicy(printer_id=P, auto_eject=True, plate_stack=2, refill=True))
    for jid in ("a", "b", "c"):
        await enqueue(a, db, jid)
    for _ in range(2):
        assert await a.schedule() == 1
        await finish(a, db, (await plates(db))[-1]["plate_id"])
    p = (await rows(db, "SELECT bed_clear, plates_left, plates_total, grams_total FROM fleet.printers WHERE printer_id = %s",
                    P))[0]
    assert p["plates_left"] == 0 and p["plates_total"] == 2 and p["grams_total"] == 40
    assert p["bed_clear"] is True                     # the second plate still had a fresh plate behind it
    out = await rows(db, "SELECT seq FROM messaging.outbox WHERE subject LIKE %s", "evt.fleet.plate_stack.empty%")
    assert len(out) == 1
    assert await a.schedule() == 1
    await finish(a, db, (await plates(db))[-1]["plate_id"])
    p = (await rows(db, "SELECT bed_clear FROM fleet.printers WHERE printer_id = %s", P))[0]
    assert p["bed_clear"] is False                    # stack empty: a person clears the bed
    await send(a, db, a.printer_policy, SetPrinterPolicy(printer_id=P, refill=True))
    assert (await rows(db, "SELECT plates_left FROM fleet.printers WHERE printer_id = %s", P))[0]["plates_left"] == 2


async def test_overdue_blocking_maintenance_keeps_the_printer_idle_until_done(agent):
    from printfarm.contracts.messages import MarkMaintenanceDone, UpsertMaintenanceTask

    a, db = agent
    await send(a, db, a.maintenance_task, UpsertMaintenanceTask(printer_id=P, task_id="clean-plate", label="Clean",
                                                               every_plates=1, block_when_overdue=True))
    await enqueue(a, db, "a")
    await enqueue(a, db, "b")
    assert await a.schedule() == 1
    await finish(a, db, (await plates(db))[-1]["plate_id"], ok=False)  # failed at 50 %: still counts as a plate
    async with db.tx() as conn:
        await conn.execute("UPDATE fleet.printers SET bed_clear = true WHERE printer_id = %s", (P,))
    assert await a.schedule() == 0                                    # overdue and blocking
    due = await rows(db, "SELECT envelope->'data'->>'overdue' AS o FROM messaging.outbox "
                         "WHERE subject LIKE %s", "evt.fleet.maintenance.due%")
    assert [d["o"] for d in due] == ["true"]
    await send(a, db, a.maintenance_done, MarkMaintenanceDone(printer_id=P, task_id="clean-plate", done_by="t", note="IPA"))
    log = await rows(db, "SELECT done_by, note, at_plates FROM fleet.maintenance_log WHERE printer_id = %s", P)
    assert log == [{"done_by": "t", "note": "IPA", "at_plates": 1}]
    assert await a.schedule() == 1


async def test_a_camera_suspicion_is_recorded_suggested_and_dismissed():
    from types import SimpleNamespace

    from printfarm.agents.telemetry.agent import TelemetryAgent
    from printfarm.agents.telemetry.vision import FakeScorer, Watch
    from printfarm.config import FleetCfg, Settings
    from printfarm.contracts.messages import DismissSuspicion
    from printfarm.store.db import Database

    db = Database(DB_URL)
    await db.open()
    await db.migrate()
    async with db.tx() as conn:
        await conn.execute("DELETE FROM telemetry.failures WHERE printer_id = %s", (P,))
        await conn.execute("DELETE FROM messaging.outbox WHERE producer = 'telemetry'")
    t = TelemetryAgent(None, db, Settings(database_url=DB_URL), FleetCfg(printers=[], materials=[], spools=[]))
    t.watch, t.scorer = Watch(frames=3), FakeScorer({P: 0.9})
    assert t.setting("vision.auto_pause") is False                     # a person decides by default
    await t._suspect(P, SimpleNamespace(plate_id="pl-cam"), 0.9)
    row = (await rows(db, "SELECT id, detector, severity, action_taken, score, resolved_at FROM telemetry.failures "
                          "WHERE printer_id = %s", P))[0]
    assert (row["detector"], row["severity"], row["action_taken"], row["resolved_at"]) == ("camera", "warning", "suggested", None)
    assert abs(row["score"] - 0.9) < 1e-6
    ev = await rows(db, "SELECT envelope->'data' AS d FROM messaging.outbox WHERE subject LIKE %s", "evt.printer.failure.suspected%")
    assert ev[0]["d"]["failure_id"] == row["id"] and ev[0]["d"]["model"] == "fake" and ev[0]["d"]["action_taken"] == "suggested"
    await send(t, db, t.dismiss_suspicion, DismissSuspicion(failure_id=row["id"], dismissed_by="dana"))
    done = (await rows(db, "SELECT resolved_by, resolved_at FROM telemetry.failures WHERE id = %s", row["id"]))[0]
    assert done["resolved_by"] == "dana" and done["resolved_at"] is not None
    await db.close()
