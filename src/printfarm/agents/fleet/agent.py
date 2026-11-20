"""Fleet Manager Agent: owns the print queue, builds plates and assigns them to printers.

Wave 2 / W2-C: the queue order comes from the pure scheduling policy (policy.py: manual order, due dates, priority,
overnight window, quiet hours, pins and holds); per-printer counters, maintenance tasks and auto plate-swap live here.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from printfarm.agents.base import Agent, on
from printfarm.agents.fleet import maintenance as mt
from printfarm.agents.fleet.policy import QPrinter, order_for, pick_plate, qjob, quiet_now, window_from
from printfarm.contracts.messages import (
    CalibrationFinished,
    CalibrationStarted,
    CancelOrderJobs,
    ControlJob,
    EnqueueJob,
    JobCompleted,
    JobControlled,
    JobFailed,
    JobQueued,
    JobShare,
    JobStarted,
    MaintenanceDone,
    MaintenanceDue,
    MarkBedCleared,
    MarkMaintenanceDone,
    PlateFinished,
    PlateScheduled,
    PlateStackEmpty,
    PrinterConnectivity,
    PrinterPolicyChanged,
    PrintFinished,
    PrintStarted,
    SetPrinterPolicy,
    StartPrint,
    UpsertMaintenanceTask,
)


def task_of(r) -> mt.Task:
    return mt.Task(r["printer_id"], r["task_id"], r["label"], r["every_hours"], r["every_plates"], r["every_days"],
                   r["block_when_overdue"], r["last_done_at"], r["last_done_seconds"], r["last_done_plates"])


def counters_of(r) -> mt.Counters:
    return mt.Counters(int(r["print_seconds_total"]), int(r["plates_total"]))


class FleetAgent(Agent):
    name = "fleet"

    async def on_start(self) -> None:
        self._tz = ZoneInfo(self.settings.business_tz)
        self._mstate: dict[tuple[str, str], str] = {}
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
                # Default maintenance tasks, once per printer (the owner may change or archive them later).
                if not await (await conn.execute("SELECT 1 FROM fleet.maintenance_tasks WHERE printer_id = %s",
                                                 (p.id,))).fetchone():
                    for t in mt.DEFAULT_TASKS:
                        await conn.execute(
                            "INSERT INTO fleet.maintenance_tasks (printer_id, task_id, label, every_hours, every_plates, "
                            "every_days, last_done_at, last_done_seconds, last_done_plates) "
                            "SELECT %s, %s, %s, %s, %s, %s, now(), print_seconds_total, plates_total "
                            "FROM fleet.printers WHERE printer_id = %s ON CONFLICT DO NOTHING",
                            (p.id, t["task_id"], t["label"], t.get("every_hours"), t.get("every_plates"),
                             t.get("every_days"), p.id))
            # Start from today's states: only changes from here on are announced.
            await self._check_maintenance(conn, announce=False)
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

    @on("cmd.fleet.control_job")
    async def control_job(self, env, cmd: ControlJob, conn):
        """Hold / release, pin / unpin, manual order (top, up, down) or back to the automatic order. Queued jobs only:
        a job already on a plate prints as planned."""
        job = await (await conn.execute("SELECT job_id, rank FROM fleet.jobs WHERE job_id = %s AND status = 'queued' "
                                        "FOR UPDATE", (cmd.job_id,))).fetchone()
        if job is None:
            self.log.warning("control %s on job %s ignored: it is not queued", cmd.action, cmd.job_id)
            return
        if cmd.action in ("hold", "release"):
            await conn.execute("UPDATE fleet.jobs SET hold = %s, updated_at = now() WHERE job_id = %s",
                               (cmd.action == "hold", cmd.job_id))
        elif cmd.action == "pin":
            if not await (await conn.execute("SELECT 1 FROM fleet.printers WHERE printer_id = %s",
                                             (cmd.printer_id,))).fetchone():
                self.log.warning("pin of job %s ignored: no printer %s", cmd.job_id, cmd.printer_id)
                return
            await conn.execute("UPDATE fleet.jobs SET pinned_printer = %s, updated_at = now() WHERE job_id = %s",
                               (cmd.printer_id, cmd.job_id))
        elif cmd.action == "unpin":
            await conn.execute("UPDATE fleet.jobs SET pinned_printer = NULL, updated_at = now() WHERE job_id = %s",
                               (cmd.job_id,))
        else:
            ranked = [r["job_id"] for r in await (await conn.execute(
                "SELECT job_id FROM fleet.jobs WHERE status = 'queued' AND rank IS NOT NULL ORDER BY rank, created_at "
                "FOR UPDATE")).fetchall()]
            ranked = manual_order(ranked, cmd.job_id, cmd.action)
            await conn.execute("UPDATE fleet.jobs SET rank = NULL, updated_at = now() WHERE job_id = %s "
                               "AND NOT (job_id = ANY(%s))", (cmd.job_id, ranked))
            for i, jid in enumerate(ranked, start=1):
                await conn.execute("UPDATE fleet.jobs SET rank = %s, updated_at = now() WHERE job_id = %s", (i, jid))
        await self.emit(conn, JobControlled(job_id=cmd.job_id, action=cmd.action, printer_id=cmd.printer_id,
                                            by=cmd.changed_by or "operator"), cause=env)
        self._kick.set()

    @on("cmd.fleet.printer_policy")
    async def printer_policy(self, env, cmd: SetPrinterPolicy, conn):
        row = await (await conn.execute(
            "UPDATE fleet.printers SET auto_eject = coalesce(%s, auto_eject), plate_stack = coalesce(%s, plate_stack), "
            "updated_at = now() WHERE printer_id = %s RETURNING auto_eject, plate_stack, plates_left",
            (cmd.auto_eject, cmd.plate_stack, cmd.printer_id))).fetchone()
        if row is None:
            self.log.warning("policy for unknown printer %s ignored", cmd.printer_id)
            return
        left = row["plate_stack"] if cmd.refill else min(row["plates_left"], row["plate_stack"])
        await conn.execute("UPDATE fleet.printers SET plates_left = %s WHERE printer_id = %s", (left, cmd.printer_id))
        await self.emit(conn, PrinterPolicyChanged(printer_id=cmd.printer_id, auto_eject=row["auto_eject"],
                                                   plate_stack=row["plate_stack"], plates_left=left,
                                                   by=cmd.changed_by or "operator"), cause=env)
        self._kick.set()

    @on("cmd.fleet.maintenance_task")
    async def maintenance_task(self, env, cmd: UpsertMaintenanceTask, conn):
        p = await (await conn.execute("SELECT print_seconds_total, plates_total FROM fleet.printers WHERE printer_id = %s",
                                      (cmd.printer_id,))).fetchone()
        if p is None:
            self.log.warning("maintenance task for unknown printer %s ignored", cmd.printer_id)
            return
        # A new task counts from now; a changed one keeps its last-done point.
        await conn.execute(
            "INSERT INTO fleet.maintenance_tasks (printer_id, task_id, label, every_hours, every_plates, every_days, "
            "block_when_overdue, archived, last_done_at, last_done_seconds, last_done_plates) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s) ON CONFLICT (printer_id, task_id) DO UPDATE SET "
            "label = EXCLUDED.label, every_hours = EXCLUDED.every_hours, every_plates = EXCLUDED.every_plates, "
            "every_days = EXCLUDED.every_days, block_when_overdue = EXCLUDED.block_when_overdue, "
            "archived = EXCLUDED.archived",
            (cmd.printer_id, cmd.task_id, cmd.label, cmd.every_hours, cmd.every_plates, cmd.every_days,
             cmd.block_when_overdue, cmd.archived, p["print_seconds_total"], p["plates_total"]))
        await self._check_maintenance(conn, cmd.printer_id)
        self._kick.set()

    @on("cmd.fleet.maintenance_done")
    async def maintenance_done(self, env, cmd: MarkMaintenanceDone, conn):
        row = await (await conn.execute(
            "UPDATE fleet.maintenance_tasks t SET last_done_at = now(), last_done_seconds = p.print_seconds_total, "
            "last_done_plates = p.plates_total FROM fleet.printers p WHERE p.printer_id = t.printer_id "
            "AND t.printer_id = %s AND t.task_id = %s RETURNING t.label, t.last_done_seconds, t.last_done_plates",
            (cmd.printer_id, cmd.task_id))).fetchone()
        if row is None:
            self.log.warning("maintenance done for unknown task %s/%s ignored", cmd.printer_id, cmd.task_id)
            return
        by = cmd.done_by or "operator"
        await conn.execute(
            "INSERT INTO fleet.maintenance_log (printer_id, task_id, done_by, note, at_seconds, at_plates) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (cmd.printer_id, cmd.task_id, by, cmd.note, row["last_done_seconds"], row["last_done_plates"]))
        self._mstate[(cmd.printer_id, cmd.task_id)] = "ok"
        await self.emit(conn, MaintenanceDone(printer_id=cmd.printer_id, task_id=cmd.task_id, label=row["label"], by=by),
                        cause=env)
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
        grams = plate["est_grams"] * (1.0 if ev.succeeded else ev.progress)
        started, finished = plate["started_at"], plate["finished_at"]
        print_seconds = max(int((finished - started).total_seconds()) if started and finished else 0, 0)
        pol = await (await conn.execute("SELECT auto_eject, plates_left FROM fleet.printers WHERE printer_id = %s "
                                        "FOR UPDATE", (ev.printer_id,))).fetchone()
        # Auto plate-swap: after a good print, a printer with a plate changer ejects the plate and takes a fresh one
        # while its stack has plates. A failed print always waits for a person.
        eject = bool(pol and ev.succeeded and pol["auto_eject"] and pol["plates_left"] > 0)
        await conn.execute(
            "UPDATE fleet.printers SET busy = false, bed_clear = %s, plates_left = plates_left - %s, "
            "plates_total = plates_total + 1, print_seconds_total = print_seconds_total + %s, "
            "grams_total = grams_total + %s, updated_at = now() WHERE printer_id = %s",
            (self.fleet.auto_clear_bed or eject or (not ev.succeeded and ev.progress == 0 and not ev.bed_state_unknown),
             1 if eject else 0, print_seconds, grams, ev.printer_id),
        )
        if eject and pol["plates_left"] == 1:
            await self.emit(conn, PlateStackEmpty(printer_id=ev.printer_id), cause=env)
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
        await self.emit(
            conn,
            PlateFinished(
                plate_id=ev.plate_id, printer_id=ev.printer_id, material=plate["material"], color=plate["color"],
                succeeded=ev.succeeded, grams_used=round(grams, 1), print_seconds=print_seconds,
                job_shares=shares, printer_watts=self._watts(ev.printer_id),
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
        await self._check_maintenance(conn, ev.printer_id)
        if not self.fleet.auto_clear_bed and not eject and ev.succeeded:
            self.log.warning("printer %s finished plate %s - waiting for bed to be cleared", ev.printer_id, ev.plate_id)
        self._kick.set()

    # ------------------------------------------------------------ maintenance

    async def _check_maintenance(self, conn, printer_id: str | None = None, *, announce: bool = True) -> set[str]:
        """Each task's state now; announce the ones that got worse (once). Returns the printers an overdue blocking
        task keeps out of the schedule."""
        rows = await (await conn.execute(
            "SELECT t.*, p.print_seconds_total, p.plates_total "
            "FROM fleet.maintenance_tasks t JOIN fleet.printers p USING (printer_id) "
            "WHERE NOT t.archived AND (%s::text IS NULL OR t.printer_id = %s)", (printer_id, printer_id))).fetchall()
        now = datetime.now(UTC)
        blocked: set[str] = set()
        for r in rows:
            st = mt.status(task_of(r), counters_of(r), now)
            key = (r["printer_id"], r["task_id"])
            before = self._mstate.get(key, "ok")
            self._mstate[key] = st.state
            if st.blocks:
                blocked.add(r["printer_id"])
            if announce and mt.crossed(before, st.state):
                await self.emit(conn, MaintenanceDue(printer_id=r["printer_id"], task_id=r["task_id"], label=r["label"],
                                                     overdue=st.state == "overdue", blocks=st.blocks))
        return blocked

    # ------------------------------------------------------------ scheduling

    def _watts(self, printer_id: str) -> float | None:
        """The printer's configured power draw (fleet.toml ``watts``), or None to use the finance default."""
        cfg = next((p for p in self.fleet.printers if p.id == printer_id), None)
        return cfg.watts if cfg and cfg.watts > 0 else None

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
        """Fill every free printer with one plate, in the order of the scheduling policy. Safe to run concurrently
        (row locks)."""
        scheduled = 0
        window = window_from(self.setting)
        now = datetime.now(UTC)
        async with self.db.tx() as conn:
            blocked = await self._check_maintenance(conn)
            if quiet_now(now, window, self._tz):
                return 0
            printers = await (
                await conn.execute(
                    "SELECT * FROM fleet.printers WHERE online AND NOT busy AND bed_clear AND NOT calibrating "
                    "ORDER BY printer_id FOR UPDATE SKIP LOCKED"
                )
            ).fetchall()
            for p in printers:
                if p["printer_id"] in blocked:
                    continue
                rows = await (
                    await conn.execute(
                        "SELECT * FROM fleet.jobs WHERE status = 'queued' AND NOT hold AND material = %s AND color = %s "
                        "AND (pinned_printer IS NULL OR pinned_printer = %s) "
                        "ORDER BY rank NULLS LAST, priority DESC, due_by NULLS LAST, created_at LIMIT 300 "
                        "FOR UPDATE SKIP LOCKED",
                        (p["material"], p["color"], p["printer_id"]),
                    )
                ).fetchall()
                if not rows:
                    continue
                by_id = {r["job_id"]: r for r in rows}
                printer = QPrinter(p["printer_id"], p["material"], p["color"], (p["bed_w"], p["bed_d"]), now)
                pick = pick_plate(printer, order_for(printer, [qjob(r) for r in rows], now, window, self._tz),
                                  margin=self.fleet.edge_margin_mm, spacing=self.fleet.part_spacing_mm,
                                  max_minutes=self.fleet.max_plate_minutes)
                if pick is None:
                    self.log.warning("queued jobs for %s/%s do not fit printer %s", p["material"], p["color"], p["printer_id"])
                    continue
                plate_id = f"plate-{uuid.uuid4().hex[:10]}"
                await conn.execute(
                    "INSERT INTO fleet.plates (plate_id, printer_id, material, color, status, est_grams, est_minutes) "
                    "VALUES (%s, %s, %s, %s, 'scheduled', %s, %s)",
                    (plate_id, p["printer_id"], p["material"], p["color"], pick.est_grams, pick.est_minutes),
                )
                for pl in pick.placements:
                    await conn.execute(
                        "INSERT INTO fleet.plate_items (plate_id, job_id, x, y, w, d, rotated) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (plate_id, pl.item_id, pl.x, pl.y, pl.w, pl.d, pl.rotated),
                    )
                job_ids = list(pick.jobs)
                await conn.execute(
                    "UPDATE fleet.jobs SET status = 'scheduled', plate_id = %s, rank = NULL, updated_at = now() "
                    "WHERE job_id = ANY(%s)",
                    (plate_id, job_ids),
                )
                await conn.execute("UPDATE fleet.printers SET busy = true WHERE printer_id = %s", (p["printer_id"],))
                await self.emit(
                    conn,
                    PlateScheduled(
                        plate_id=plate_id, printer_id=p["printer_id"], job_ids=job_ids, material=p["material"],
                        color=p["color"], est_grams=pick.est_grams, est_minutes=pick.est_minutes,
                    ),
                    correlationid=plate_id,
                )
                await self.emit(
                    conn,
                    StartPrint(printer_id=p["printer_id"], plate_id=plate_id,
                               artifact_uris=[by_id[j]["artifact_uri"] for j in job_ids]),
                    correlationid=plate_id,
                )
                self.log.info("plate %s -> %s: %d job(s) (%s)", plate_id, p["printer_id"], len(job_ids),
                              pick.reasons.get(job_ids[0], ""))
                scheduled += 1
        if scheduled:
            self.relay.wakeup.set()
        return scheduled


def manual_order(ranked: list[str], job_id: str, action: str) -> list[str]:
    """The jobs in the manual order after ``action`` on ``job_id`` (pure). A job not in the manual order yet joins it
    at the end on up / down, at the start on top; auto takes it out."""
    out = [j for j in ranked if j != job_id]
    if action == "auto":
        return out
    if action == "top":
        return [job_id, *out]
    if job_id not in ranked:
        return [*out, job_id]
    i = ranked.index(job_id)
    i = max(0, i - 1) if action == "up" else min(len(out), i + 1)
    out.insert(i, job_id)
    return out
