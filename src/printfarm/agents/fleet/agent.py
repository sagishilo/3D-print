"""Fleet Manager Agent: owns the print queue, builds plates and assigns them to printers."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from printfarm.agents.base import Agent, on
from printfarm.agents.fleet.packing import JobSpec, build_plate
from printfarm.contracts.messages import (
    CalibrationFinished,
    CalibrationStarted,
    CancelOrderJobs,
    EnqueueJob,
    JobCompleted,
    JobFailed,
    JobQueued,
    JobShare,
    JobStarted,
    MarkBedCleared,
    PlateFinished,
    PlateScheduled,
    PrinterConnectivity,
    PrintFinished,
    PrintStarted,
    StartPrint,
)


class FleetAgent(Agent):
    name = "fleet"

    async def on_start(self) -> None:
        async with self.db.tx() as conn:
            for p in self.fleet.printers:
                await conn.execute(
                    "INSERT INTO fleet.printers (printer_id, vendor, bed_w, bed_d, nozzle_mm, material, color, model, simulated) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (printer_id) DO UPDATE SET "
                    "vendor = EXCLUDED.vendor, bed_w = EXCLUDED.bed_w, bed_d = EXCLUDED.bed_d, "
                    "nozzle_mm = EXCLUDED.nozzle_mm, material = EXCLUDED.material, color = EXCLUDED.color, "
                    "model = EXCLUDED.model, simulated = EXCLUDED.simulated",
                    (p.id, p.vendor, p.bed_mm[0], p.bed_mm[1], p.nozzle_mm, p.material, p.color, p.model, p.simulated),
                )
        self._kick = asyncio.Event()
        self.spawn(self._scheduler_loop())

    # ------------------------------------------------------------ commands

    @on("cmd.fleet.enqueue_job")
    async def enqueue(self, env, cmd: EnqueueJob, conn):
        cur = await conn.execute(
            "INSERT INTO fleet.jobs (job_id, order_id, correlationid, artifact_uri, material, color, footprint_w, "
            "footprint_d, est_grams, est_minutes, due_by, priority, product_type, whole_plate, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued') ON CONFLICT DO NOTHING",
            (cmd.job_id, cmd.order_id, env.correlationid, cmd.artifact_uri, cmd.material, cmd.color,
             cmd.footprint_mm[0], cmd.footprint_mm[1], cmd.est_grams, cmd.est_minutes, cmd.due_by, cmd.priority,
             cmd.product_type, cmd.whole_plate),
        )
        if cur.rowcount:
            await self.emit(conn, JobQueued(job_id=cmd.job_id, order_id=cmd.order_id), cause=env)
        self._kick.set()

    @on("cmd.fleet.cancel_jobs")
    async def cancel_jobs(self, env, cmd: CancelOrderJobs, conn):
        """A cancelled order: its queued jobs are never scheduled. Jobs already on a plate print as planned."""
        rows = await (await conn.execute(
            "UPDATE fleet.jobs SET status = 'cancelled', updated_at = now() WHERE order_id = %s AND status = 'queued' "
            "RETURNING job_id", (cmd.order_id,))).fetchall()
        self.log.info("order %s cancelled: %d queued job(s) dropped", cmd.order_id, len(rows))

    @on("cmd.fleet.mark_bed_cleared")
    async def bed_cleared(self, env, cmd: MarkBedCleared, conn):
        await conn.execute("UPDATE fleet.printers SET bed_clear = true WHERE printer_id = %s", (cmd.printer_id,))
        self._kick.set()

    # ------------------------------------------------------------ printer lifecycle (from telemetry)

    # from_start: on a fresh install every agent starts at once, and telemetry reports its printers online before
    # this consumer exists. Reading the stored history on first start (in order, the last event wins) keeps
    # fleet.printers.online right; otherwise the scheduler would wait for a reconnect that never comes.
    @on("evt.printer.connection.changed", from_start=True)
    async def connectivity(self, env, ev: PrinterConnectivity, conn):
        await conn.execute(
            "UPDATE fleet.printers SET online = %s, updated_at = now() WHERE printer_id = %s", (ev.online, ev.printer_id)
        )
        self._kick.set()

    # A calibrating printer is moving on its own: the scheduler must not send it a plate meanwhile.
    @on("evt.printer.calibration.started")
    async def calibration_started(self, env, ev: CalibrationStarted, conn):
        await conn.execute("UPDATE fleet.printers SET calibrating = true, updated_at = now() WHERE printer_id = %s",
                           (ev.printer_id,))

    @on("evt.printer.calibration.finished")
    async def calibration_finished(self, env, ev: CalibrationFinished, conn):
        if ev.started:  # a refused request never set the flag
            await conn.execute("UPDATE fleet.printers SET calibrating = false, updated_at = now() "
                               "WHERE printer_id = %s", (ev.printer_id,))
            self._kick.set()

    @on("evt.printer.print.started")
    async def print_started(self, env, ev: PrintStarted, conn):
        await conn.execute(
            "UPDATE fleet.plates SET status = 'printing', started_at = now() WHERE plate_id = %s AND status = 'scheduled'",
            (ev.plate_id,),
        )
        jobs = await (
            await conn.execute(
                "UPDATE fleet.jobs SET status = 'printing', updated_at = now() WHERE plate_id = %s AND status = 'scheduled' "
                "RETURNING job_id, order_id, correlationid",
                (ev.plate_id,),
            )
        ).fetchall()
        for j in jobs:
            await self.emit(
                conn,
                JobStarted(job_id=j["job_id"], order_id=j["order_id"], plate_id=ev.plate_id, printer_id=ev.printer_id),
                cause=env, correlationid=j["correlationid"],
            )

    @on("evt.printer.print.finished")
    async def print_finished(self, env, ev: PrintFinished, conn):
        plate = await (
            await conn.execute(
                "UPDATE fleet.plates SET status = %s, finished_at = now() WHERE plate_id = %s "
                "AND status IN ('scheduled', 'printing') RETURNING *",
                ("finished" if ev.succeeded else "failed", ev.plate_id),
            )
        ).fetchone()
        if plate is None:
            self.log.warning("finish for unknown/closed plate %s", ev.plate_id)
            return
        await conn.execute(
            "UPDATE fleet.printers SET busy = false, bed_clear = %s, updated_at = now() WHERE printer_id = %s",
            (self.fleet.auto_clear_bed or not ev.succeeded and ev.progress == 0, ev.printer_id),
        )
        grams = plate["est_grams"] * (1.0 if ev.succeeded else ev.progress)
        on_plate = await (
            await conn.execute(
                "SELECT job_id, order_id, product_type, est_grams FROM fleet.jobs WHERE plate_id = %s ORDER BY job_id",
                (ev.plate_id,),
            )
        ).fetchall()
        planned = sum(j["est_grams"] for j in on_plate)
        shares = [
            JobShare(job_id=j["job_id"], order_id=j["order_id"], product_type=j["product_type"],
                     grams=round(grams * j["est_grams"] / planned, 2) if planned else 0.0)
            for j in on_plate
        ]
        started, finished = plate["started_at"], plate["finished_at"]
        print_seconds = int((finished - started).total_seconds()) if started and finished else 0
        await self.emit(
            conn,
            PlateFinished(
                plate_id=ev.plate_id, printer_id=ev.printer_id, material=plate["material"], color=plate["color"],
                succeeded=ev.succeeded, grams_used=round(grams, 1), print_seconds=max(print_seconds, 0),
                job_shares=shares,
            ),
            cause=env,
        )
        jobs = await (
            await conn.execute(
                "UPDATE fleet.jobs SET status = %s, updated_at = now() WHERE plate_id = %s "
                "AND status IN ('scheduled', 'printing') RETURNING job_id, order_id, correlationid",
                ("completed" if ev.succeeded else "failed", ev.plate_id),
            )
        ).fetchall()
        for j in jobs:
            out = (
                JobCompleted(job_id=j["job_id"], order_id=j["order_id"], plate_id=ev.plate_id)
                if ev.succeeded
                else JobFailed(job_id=j["job_id"], order_id=j["order_id"], plate_id=ev.plate_id,
                               reason=ev.reason or "print failed")
            )
            await self.emit(conn, out, cause=env, correlationid=j["correlationid"])
        if not self.fleet.auto_clear_bed and ev.succeeded:
            self.log.warning("printer %s finished plate %s - waiting for bed to be cleared", ev.printer_id, ev.plate_id)
        self._kick.set()

    # ------------------------------------------------------------ scheduling

    async def _scheduler_loop(self) -> None:
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._kick.wait(), self.fleet.schedule_interval_s)
                # Batching window: jobs of one order (and bursts of orders) arrive as
                # separate commands; wait briefly so they can share a plate.
                await asyncio.sleep(self.fleet.batch_window_s)
            self._kick.clear()
            try:
                n = await self.schedule()
                if n:
                    self.log.info("scheduled %d plate(s)", n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.exception("scheduling failed: %r", exc)

    async def schedule(self) -> int:
        """Fill every free printer with one plate. Safe to run concurrently (row locks)."""
        scheduled = 0
        async with self.db.tx() as conn:
            printers = await (
                await conn.execute(
                    "SELECT * FROM fleet.printers WHERE online AND NOT busy AND bed_clear AND NOT calibrating "
                    "ORDER BY printer_id FOR UPDATE SKIP LOCKED"
                )
            ).fetchall()
            for p in printers:
                rows = await (
                    await conn.execute(
                        "SELECT * FROM fleet.jobs WHERE status = 'queued' AND material = %s AND color = %s "
                        "ORDER BY priority DESC, due_by NULLS LAST, created_at LIMIT 200 FOR UPDATE SKIP LOCKED",
                        (p["material"], p["color"]),
                    )
                ).fetchall()
                if not rows:
                    continue
                by_id = {r["job_id"]: r for r in rows}
                # A sliced file is a whole plate already: when it is next in line it prints alone; otherwise the
                # plate is packed from the jobs that can share one.
                if rows[0]["whole_plate"]:
                    rows = rows[:1]
                else:
                    rows = [r for r in rows if not r["whole_plate"]]
                plan = build_plate(
                    [JobSpec(r["job_id"], r["footprint_w"], r["footprint_d"], r["est_minutes"], r["est_grams"],
                             r["priority"], r["due_by"], r["created_at"]) for r in rows],
                    (p["bed_w"], p["bed_d"]),
                    margin=self.fleet.edge_margin_mm, spacing=self.fleet.part_spacing_mm,
                    max_minutes=self.fleet.max_plate_minutes,
                )
                if plan is None:
                    self.log.warning("queued jobs for %s/%s do not fit printer %s", p["material"], p["color"], p["printer_id"])
                    continue
                plate_id = f"plate-{uuid.uuid4().hex[:10]}"
                await conn.execute(
                    "INSERT INTO fleet.plates (plate_id, printer_id, material, color, status, est_grams, est_minutes) "
                    "VALUES (%s, %s, %s, %s, 'scheduled', %s, %s)",
                    (plate_id, p["printer_id"], p["material"], p["color"], plan.est_grams, plan.est_minutes),
                )
                for pl in plan.placements:
                    await conn.execute(
                        "INSERT INTO fleet.plate_items (plate_id, job_id, x, y, w, d, rotated) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (plate_id, pl.item_id, pl.x, pl.y, pl.w, pl.d, pl.rotated),
                    )
                job_ids = [pl.item_id for pl in plan.placements]
                await conn.execute(
                    "UPDATE fleet.jobs SET status = 'scheduled', plate_id = %s, updated_at = now() WHERE job_id = ANY(%s)",
                    (plate_id, job_ids),
                )
                await conn.execute("UPDATE fleet.printers SET busy = true WHERE printer_id = %s", (p["printer_id"],))
                await self.emit(
                    conn,
                    PlateScheduled(
                        plate_id=plate_id, printer_id=p["printer_id"], job_ids=job_ids, material=p["material"],
                        color=p["color"], est_grams=plan.est_grams, est_minutes=plan.est_minutes,
                    ),
                    correlationid=plate_id,
                )
                await self.emit(
                    conn,
                    StartPrint(printer_id=p["printer_id"], plate_id=plate_id,
                               artifact_uris=[by_id[j]["artifact_uri"] for j in job_ids]),
                    correlationid=plate_id,
                )
                self.log.info("plate %s -> %s: %d job(s), %.0f%% bed", plate_id, p["printer_id"], len(job_ids),
                              plan.utilization * 100)
                scheduled += 1
        if scheduled:
            self.relay.wakeup.set()
        return scheduled
