"""Printer Telemetry Agent: the only component that talks to printers.

Hot path (per telemetry sample, no DB, no LLM):
    adapter -> canonical PrinterState -> detectors -> CRITICAL? pause the printer NOW, then verify the pause
Warm path (only on change):
    lifecycle transitions + failures -> Postgres + outbox -> EVENTS stream
Live view (throttled to 1 Hz per printer):
    PrinterState -> TELEMETRY stream (tlm.<printer>.state) + KV printer_status
Plate starts (off the hot path; recorded in telemetry.plate_starts, so a restart resumes them):
    StartPrint -> plate file -> upload (FTPS / HTTP) -> start command -> printer reports the plate -> started
    any step fails -> PrintFinished(succeeded=False, reason) so the Fleet Manager can reschedule

Every pause - automatic (CRITICAL finding), from the dashboard, or "pause all" - goes through the same path:
send, then watch the printer's own state until it stops printing (safety.pause_and_verify). The outcome is
recorded in telemetry.pause_checks and published as evt.printer.pause.confirmed or, as a safety alert,
evt.printer.pause.unconfirmed.

Calibrations (cmd.telemetry.calibrate) run only on an online, idle printer whose bed the operator confirmed empty,
one at a time and never while a plate is being started. Each request ends in evt.printer.calibration.finished
(also when refused) and is recorded in telemetry.calibrations. A critical finding during a calibration aborts it.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.telemetry.adapters import CalibrationError, PrinterAdapter, make_adapter
from printfarm.agents.telemetry.detectors import DetectorSet, Finding
from printfarm.agents.telemetry.lost import LostPrintWatch
from printfarm.agents.telemetry.platefiles import PlateFileError, check_fits, resolve_plate_file
from printfarm.agents.telemetry.safety import PauseOutcome, pause_and_verify
from printfarm.agents.telemetry.transfer import PlateFile, UploadError
from printfarm.agents.telemetry.vision import FRAME_EVERY_S, FakeScorer, HttpScorer, Scorer, Watch, model_name
from printfarm.contracts import Envelope, deterministic_id, new_message
from printfarm.contracts.calibration import BY_KEY as ROUTINES
from printfarm.contracts.calibration import target_temperature
from printfarm.contracts.messages import (
    CalibratePrinter,
    CalibrationFinished,
    CalibrationStarted,
    CancelPrint,
    DismissSuspicion,
    FailureDetected,
    FailureSuspected,
    PauseAll,
    PauseConfirmed,
    PausePrint,
    PauseUnconfirmed,
    PlateUploaded,
    PrinterConnectivity,
    PrinterState,
    PrinterStatus,
    PrintFinished,
    PrintStarted,
    ResumePrint,
    Severity,
    StartPrint,
)

ACTIVE = {PrinterStatus.PRINTING, PrinterStatus.PAUSED}
DONE = {PrinterStatus.FINISHED, PrinterStatus.FAILED}
PUBLISH_EVERY_S = 1.0
UPLOAD_ATTEMPTS = 3
ONLINE_WAIT_S = 30.0


CALIBRATION_OK = {PrinterStatus.IDLE, PrinterStatus.FINISHED, PrinterStatus.FAILED}


class StartFailed(RuntimeError):
    """The printer did not take the plate; the message is shown to the operator."""


def calibration_refusal(cmd: CalibratePrinter, *, vendor: str | None, online: bool, state: PrinterState | None,
                        busy: str | None) -> str | None:
    """Why ``cmd`` must not run now (None = it may). Pure: unit-tested in tests/test_calibration.py."""
    routine = ROUTINES.get(cmd.routine)
    if vendor is None:
        return "unknown printer"
    if routine is None or vendor not in routine.vendors:
        return f"{cmd.routine} is not available on this printer"
    if not cmd.bed_clear_confirmed:
        return "the bed was not confirmed empty"
    try:
        target_temperature(routine, cmd.temperature)
    except ValueError as exc:
        return str(exc)
    if not online:
        return "printer offline"
    if state is None:
        return "no state from the printer yet"
    if state.status not in CALIBRATION_OK:
        return f"printer is {state.status.value}"
    return busy


class TelemetryAgent(Agent):
    name = "telemetry"

    async def on_start(self) -> None:
        self.adapters: dict[str, PrinterAdapter] = {}
        self.detectors: dict[str, DetectorSet] = {}
        self.last: dict[str, PrinterState] = {}
        self.cfgs = {cfg.id: cfg for cfg in self.fleet.printers}
        self._last_pub: dict[str, float] = {}
        self._pausing: set[str] = set()                 # printers with a pause check in flight
        self._starting: dict[tuple[str, str], asyncio.Task] = {}
        self._calibrating: dict[str, str] = {}          # printer -> calibration id in flight
        # open sessions before any printer reports (adapters start below)
        self.lost = LostPrintWatch()
        async with self.db.tx() as conn:
            for r in await (await conn.execute(
                    "SELECT printer_id, plate_id FROM telemetry.print_sessions WHERE finished_at IS NULL")).fetchall():
                self.lost.opened(r["printer_id"], r["plate_id"])
        for cfg in self.fleet.printers:
            pid = cfg.id
            self.detectors[pid] = DetectorSet()
            self.adapters[pid] = make_adapter(
                cfg,
                on_state=lambda s, pid=pid: self._on_state(pid, s),
                on_connection=lambda up, pid=pid: self._on_connection(pid, up),
            )
            self.spawn(self.adapters[pid].run())
        await self._resume_open_starts()
        await self._close_interrupted_calibrations()
        self.watch = Watch()
        self.scorer = self._make_scorer()
        if self.scorer is not None:
            self.spawn(self._vision_loop())

    # ------------------------------------------------------------ device callbacks

    async def _on_connection(self, pid: str, online: bool) -> None:
        self.log.info("printer %s %s", pid, "online" if online else "OFFLINE")
        await self.emit_now(PrinterConnectivity(printer_id=pid, online=online))

    async def _on_state(self, pid: str, s: PrinterState) -> None:
        prev = self.last.get(pid)
        self.last[pid] = s

        # 1. safety first: detectors + immediate local action
        for f in self.detectors[pid].feed(s):
            await self._handle_finding(pid, s, f)

        # 2. lifecycle transitions -> durable, idempotent events
        if s.plate_id and s.status in ACTIVE and (prev is None or prev.status not in ACTIVE or prev.plate_id != s.plate_id):
            await self._lifecycle_started(pid, s.plate_id)
        if s.plate_id and s.status in DONE and (prev is None or prev.status not in DONE or prev.plate_id != s.plate_id):
            await self._lifecycle_finished(pid, s.plate_id, s.status == PrinterStatus.FINISHED, s.progress,
                                           "" if s.status == PrinterStatus.FINISHED else (s.error_code or "printer reported failure"))

        # 2b. a print that ended while we were not watching (restart, outage, cancelled at the printer)
        for plate in self.lost.check(pid, s, time.monotonic()):
            self.log.warning("plate %s on %s is lost: the printer reports %s%s; closing it as failed", plate, pid,
                             s.status.value, f" with plate {s.plate_id}" if s.plate_id else "")
            await self._lifecycle_finished(
                pid, plate, False, s.progress if s.plate_id == plate else 0.0,
                f"print lost: the printer reports {s.status.value} and no longer runs this plate "
                "(restarted, cancelled at the printer, or ended while the farm was offline); check the bed",
                bed_state_unknown=True)

        # 3. live view, throttled
        now = time.monotonic()
        if prev is None or prev.status != s.status or now - self._last_pub.get(pid, 0) >= PUBLISH_EVERY_S:
            self._last_pub[pid] = now
            env = new_message(s, source=self.source, subject=pid, correlationid=s.plate_id or pid)
            try:
                await self.bus.publish(env)
                await self.bus.kv_put("printer_status", pid, env.to_bytes())
            except Exception as exc:  # noqa: BLE001 - live view is best effort; never block the hot path
                self.log.debug("telemetry publish failed: %r", exc)

    async def _handle_finding(self, pid: str, s: PrinterState, f: Finding) -> None:
        action = "none"
        if f.severity == Severity.CRITICAL and s.status == PrinterStatus.CALIBRATING:
            try:
                await self.adapters[pid].abort()
                action = "calibration aborted"
            except Exception as exc:  # noqa: BLE001 - report it; the finding itself is still recorded
                action = f"abort failed: {exc!r}"[:200]
        elif f.severity == Severity.CRITICAL and s.status == PrinterStatus.PRINTING:
            if self.setting("safety.auto_pause"):
                # The pause task is created before any DB work below, so it is sent within one event-loop turn.
                self.pause_verified(pid, reason=f"{f.detector}: {f.message}", requested_by="telemetry")
                action = "pause_sent"
            else:
                action = "none (auto-pause is switched off)"
        self.log.error("printer %s %s: %s -> %s", pid, f.detector, f.message, action)
        async with self.db.tx() as conn:
            await conn.execute(
                "INSERT INTO telemetry.failures (printer_id, plate_id, detector, severity, message, action_taken) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (pid, s.plate_id, f.detector, f.severity.value, f.message, action),
            )
            await self.emit(
                conn,
                FailureDetected(printer_id=pid, plate_id=s.plate_id, detector=f.detector, severity=f.severity,
                                message=f.message, action_taken=action),
                correlationid=s.plate_id or pid,
            )
        self.relay.wakeup.set()

    # ------------------------------------------------------------ camera failure detection (W2-C, vision.py)

    def _make_scorer(self) -> Scorer | None:
        name = model_name()
        if name == "fake":
            return FakeScorer.from_env()
        if name == "http":
            url = os.getenv("VISION_MODEL_URL", "")
            if not url:
                self.log.warning("VISION_MODEL=http needs VISION_MODEL_URL; camera detection stays off")
                return None
            return HttpScorer(url, httpx.AsyncClient())
        return None

    async def _frame(self, client: httpx.AsyncClient, pid: str) -> bytes | None:
        try:
            r = await client.get(f"{os.getenv('GO2RTC_URL', 'http://go2rtc:1984')}/api/frame.jpeg",
                                 params={"src": pid}, timeout=10)
            return r.content if r.status_code == 200 and r.content else None
        except httpx.HTTPError:
            return None

    async def _vision_loop(self) -> None:
        """One frame per printing printer every FRAME_EVERY_S while ``vision.enabled`` is on."""
        async with httpx.AsyncClient() as client:
            while True:
                await asyncio.sleep(FRAME_EVERY_S)
                if not self.setting("vision.enabled"):
                    continue
                self.watch.threshold = float(self.setting("vision.threshold"))
                self.watch.frames = int(self.setting("vision.frames"))
                for pid, s in list(self.last.items()):
                    if s.status != PrinterStatus.PRINTING:
                        self.watch.reset(pid)
                        continue
                    try:
                        jpeg = await self._frame(client, pid) if self.scorer.needs_frame else None
                        score = await self.scorer.score(pid, jpeg)
                        if score is not None and self.watch.feed(pid, score, time.monotonic()):
                            await self._suspect(pid, s, score)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - one camera's trouble never stops the others
                        self.log.warning("camera check of %s failed: %r", pid, exc)

    async def _suspect(self, pid: str, s: PrinterState, score: float) -> None:
        action = "suggested"
        if self.setting("vision.auto_pause") and self.pause_verified(
                pid, reason=f"camera: failure likely ({score:.0%})", requested_by="telemetry"):
            action = "pause_sent"
        self.log.warning("printer %s camera: failure likely (%.2f) -> %s", pid, score, action)
        async with self.db.tx() as conn:
            row = await (await conn.execute(
                "INSERT INTO telemetry.failures (printer_id, plate_id, detector, severity, message, action_taken, score) "
                "VALUES (%s, %s, 'camera', 'warning', %s, %s, %s) RETURNING id",
                (pid, s.plate_id, f"camera: failure likely ({score:.0%} on {self.watch.frames} frames in a row)",
                 action, score))).fetchone()
            await self.emit(conn, FailureSuspected(printer_id=pid, plate_id=s.plate_id, failure_id=row["id"],
                                                   score=round(score, 3), model=self.scorer.name,
                                                   frames=self.watch.frames, action_taken=action),
                            correlationid=s.plate_id or pid)
        self.relay.wakeup.set()

    @on("cmd.telemetry.dismiss_suspicion")
    async def dismiss_suspicion(self, env, cmd: DismissSuspicion, conn):
        await conn.execute("UPDATE telemetry.failures SET resolved_at = now(), resolved_by = %s "
                           "WHERE id = %s AND resolved_at IS NULL", (cmd.dismissed_by or "operator", cmd.failure_id))

    async def _lifecycle_started(self, pid: str, plate_id: str) -> None:
        async with self.db.tx() as conn:
            cur = await conn.execute(
                "INSERT INTO telemetry.print_sessions (printer_id, plate_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (pid, plate_id),
            )
            if cur.rowcount:
                await self.emit(conn, PrintStarted(printer_id=pid, plate_id=plate_id), correlationid=plate_id,
                                id=deterministic_id(pid, plate_id, "started"))
        self.lost.opened(pid, plate_id)
        self.relay.wakeup.set()

    async def _lifecycle_finished(self, pid: str, plate_id: str, ok: bool, progress: float, reason: str,
                                  bed_state_unknown: bool = False) -> None:
        async with self.db.tx() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO telemetry.print_sessions (printer_id, plate_id, finished_at, succeeded) "
                    "VALUES (%s, %s, now(), %s) ON CONFLICT (printer_id, plate_id) DO UPDATE "
                    "SET finished_at = now(), succeeded = EXCLUDED.succeeded "
                    "WHERE telemetry.print_sessions.finished_at IS NULL RETURNING plate_id",
                    (pid, plate_id, ok),
                )
            ).fetchone()
            if row:
                await self.emit(
                    conn,
                    PrintFinished(printer_id=pid, plate_id=plate_id, succeeded=ok, progress=progress, reason=reason,
                                  bed_state_unknown=bed_state_unknown),
                    correlationid=plate_id, id=deterministic_id(pid, plate_id, "finished"),
                )
        self.lost.closed(pid, plate_id)
        self.relay.wakeup.set()

    # ------------------------------------------------------------ verified pause (safety-critical)

    def pause_verified(self, pid: str, *, reason: str, requested_by: str) -> bool:
        """Pause ``pid`` and verify it; returns False if a pause check for it is already running."""
        if pid not in self.adapters:
            self.log.warning("pause for unknown printer %s ignored", pid)
            return False
        if pid in self._pausing:
            self.log.info("pause of %s already in progress (%s)", pid, reason)
            return False
        self._pausing.add(pid)
        self.spawn(self._pause_task(pid, reason, requested_by))
        return True

    async def _pause_task(self, pid: str, reason: str, requested_by: str) -> None:
        adapter = self.adapters[pid]
        state = self.last.get(pid)
        plate_id = state.plate_id if state else None
        try:
            outcome = await pause_and_verify(
                send_pause=adapter.pause, current_state=lambda: self.last.get(pid), is_online=lambda: adapter.online,
                confirm_s=self.fleet.pause_confirm_s, retries=self.fleet.pause_retries,
            )
        finally:
            self._pausing.discard(pid)
        await self._record_pause(pid, plate_id, reason, requested_by, outcome, adapter.last_reply("pause"))

    async def _record_pause(self, pid: str, plate_id: str | None, reason: str, requested_by: str,
                            outcome: PauseOutcome, reply: str) -> None:
        latency_ms = int(outcome.elapsed_s * 1000)
        if outcome.confirmed:
            self.log.warning("printer %s pause CONFIRMED in %d ms (%s)", pid, latency_ms, outcome.status)
        else:
            self.log.critical("printer %s pause UNCONFIRMED after %d ms, %d attempt(s): still %s, online=%s, reply=%r, "
                              "errors=%s", pid, latency_ms, outcome.attempts, outcome.status, outcome.online, reply,
                              list(outcome.send_errors))
        async with self.db.tx() as conn:
            await conn.execute(
                "INSERT INTO telemetry.pause_checks (printer_id, plate_id, reason, requested_by, confirmed, latency_ms, "
                "attempts, final_status, printer_reply) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (pid, plate_id, reason[:500], requested_by, outcome.confirmed, latency_ms if outcome.confirmed else None,
                 outcome.attempts, outcome.status.value if outcome.status else None, reply or None),
            )
            if outcome.confirmed:
                await self.emit(conn, PauseConfirmed(
                    printer_id=pid, plate_id=plate_id, reason=reason, requested_by=requested_by, latency_ms=latency_ms,
                    attempts=outcome.attempts, status=outcome.status), correlationid=plate_id or pid)
            else:
                detail = (f"pause not confirmed after {outcome.attempts} attempt(s) / {latency_ms} ms: printer still "
                          f"reports {outcome.status.value if outcome.status else 'nothing'}"
                          f"{'' if outcome.online else ' and is offline'}{f'; printer replied {reply}' if reply else ''}")
                await conn.execute(
                    "INSERT INTO telemetry.failures (printer_id, plate_id, detector, severity, message, action_taken) "
                    "VALUES (%s, %s, 'pause_unconfirmed', 'critical', %s, 'alert')",
                    (pid, plate_id, detail),
                )
                await self.emit(conn, PauseUnconfirmed(
                    printer_id=pid, plate_id=plate_id, reason=reason, requested_by=requested_by, waited_ms=latency_ms,
                    attempts=outcome.attempts, last_status=outcome.status, online=outcome.online,
                    printer_reply=reply), correlationid=plate_id or pid)
        self.relay.wakeup.set()

    # ------------------------------------------------------------ plate start: upload -> start -> confirm

    @on("cmd.telemetry.start_print")
    async def start_print(self, env, cmd: StartPrint, conn):
        adapter = self.adapters.get(cmd.printer_id)
        cur = self.last.get(cmd.printer_id)
        if cur and cur.plate_id == cmd.plate_id and cur.status in ACTIVE | DONE:
            return  # already started (redelivery) - idempotent at the device
        if adapter is None or not adapter.online or cmd.printer_id in self._calibrating:
            reason = ("unknown printer" if adapter is None else "printer offline" if not adapter.online
                      else "printer is calibrating")
            await self._finish_failed(conn, cmd.printer_id, cmd.plate_id, reason, cause=env)
            return
        cur = await conn.execute(
            "INSERT INTO telemetry.plate_starts (printer_id, plate_id, artifact_uris, status) VALUES (%s, %s, %s, 'queued') "
            "ON CONFLICT DO NOTHING",
            (cmd.printer_id, cmd.plate_id, Jsonb(list(cmd.artifact_uris))),
        )
        if cur.rowcount:  # a redelivered command finds the row and leaves the running start alone
            self.after_commit(lambda: self._spawn_start(cmd.printer_id, cmd.plate_id, list(cmd.artifact_uris)))

    async def _resume_open_starts(self) -> None:
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT printer_id, plate_id, artifact_uris FROM telemetry.plate_starts "
                "WHERE status IN ('queued', 'uploading', 'starting')")).fetchall()
        for r in rows:
            self.log.warning("resuming the start of plate %s on %s (agent restarted)", r["plate_id"], r["printer_id"])
            self._spawn_start(r["printer_id"], r["plate_id"], list(r["artifact_uris"]))

    def _spawn_start(self, pid: str, plate_id: str, artifact_uris: list[str]) -> None:
        key = (pid, plate_id)
        if key in self._starting and not self._starting[key].done():
            return
        self._starting[key] = self.spawn(self._run_start(pid, plate_id, artifact_uris))
        self._starting[key].add_done_callback(lambda _t, key=key: self._starting.pop(key, None))

    async def _run_start(self, pid: str, plate_id: str, artifact_uris: list[str]) -> None:
        adapter, cfg = self.adapters.get(pid), self.cfgs.get(pid)
        try:
            if adapter is None or cfg is None:
                raise StartFailed("unknown printer")
            plate = await asyncio.to_thread(resolve_plate_file, plate_id, artifact_uris, vendor=adapter.vendor,
                                            simulated=cfg.simulated, out_dir=Path(self.settings.artifact_dir))
            check_fits(plate, printer_model=cfg.model, max_z=cfg.max_z_mm, ams_slots=cfg.ams_slots)
            if not await self._wait_for(lambda: adapter.online, ONLINE_WAIT_S):
                raise StartFailed(f"printer offline for {ONLINE_WAIT_S:.0f} s")
            if self._is_running(pid, plate_id):  # e.g. resumed after a restart and the printer already has it
                await self._start_status(pid, plate_id, "started")
                return
            seconds = await self._upload(pid, adapter, plate)
            await self.emit_now(PlateUploaded(printer_id=pid, plate_id=plate_id, remote_file=plate.remote_name,
                                              bytes=plate.size, md5=plate.md5, seconds=round(seconds, 3)),
                                correlationid=plate_id)
            await self._start_status(pid, plate_id, "starting")
            await adapter.start_plate(plate)
            if not await self._wait_for(lambda: self._is_running(pid, plate_id), self.fleet.start_confirm_s):
                reply = adapter.last_reply("project_file")
                try:  # don't let a late start print a plate the fleet has already given up on
                    await adapter.cancel()
                except Exception:  # noqa: BLE001
                    pass
                raise StartFailed(f"printer did not start the plate within {self.fleet.start_confirm_s:.0f} s"
                                  + (f" (printer replied {reply})" if reply else " (no reply: command ignored?)"))
            s = self.last.get(pid)
            if s and s.status == PrinterStatus.FAILED and s.progress == 0:  # refused at once (missing file, bad md5)
                raise StartFailed(f"printer refused the plate: {adapter.last_reply('project_file') or s.error_code}")
            await self._start_status(pid, plate_id, "started")
            self.log.info("plate %s started on %s", plate_id, pid)
        except asyncio.CancelledError:
            raise
        except (PlateFileError, UploadError, StartFailed) as exc:
            await self._fail_start(pid, plate_id, str(exc))
        except Exception as exc:  # noqa: BLE001 - connection drops etc.: fail the plate so it can be rescheduled
            self.log.exception("start of %s on %s failed", plate_id, pid)
            await self._fail_start(pid, plate_id, f"{type(exc).__name__}: {exc}")

    async def _upload(self, pid: str, adapter: PrinterAdapter, plate: PlateFile) -> float:
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            await self._start_status(pid, plate.plate_id, "uploading", plate=plate, attempt=True)
            try:
                seconds = await adapter.upload(plate, self.fleet.upload_timeout_s)
                self.log.info("uploaded %s (%d bytes) to %s in %.1f s", plate.remote_name, plate.size, pid, seconds)
                return seconds
            except UploadError as exc:
                if not exc.transient or attempt == UPLOAD_ATTEMPTS:
                    raise
                self.log.warning("upload of %s to %s failed (attempt %d/%d): %s", plate.remote_name, pid, attempt,
                                 UPLOAD_ATTEMPTS, exc)
                await asyncio.sleep(2 * attempt)
        raise AssertionError("unreachable")

    def _is_running(self, pid: str, plate_id: str) -> bool:
        s = self.last.get(pid)
        return bool(s and s.plate_id == plate_id and s.status in ACTIVE | DONE)

    @staticmethod
    async def _wait_for(predicate, timeout: float, poll_s: float = 0.2) -> bool:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(poll_s)
        return True

    async def _start_status(self, pid: str, plate_id: str, status: str, *, plate: PlateFile | None = None,
                            attempt: bool = False, error: str | None = None) -> None:
        async with self.db.tx() as conn:
            await conn.execute(
                "UPDATE telemetry.plate_starts SET status = %s, remote_file = coalesce(%s, remote_file), "
                "md5 = coalesce(%s, md5), bytes = coalesce(%s, bytes), attempts = attempts + %s, error = %s, "
                "updated_at = now() WHERE printer_id = %s AND plate_id = %s",
                (status, plate.remote_name if plate else None, plate.md5 if plate else None, plate.size if plate else None,
                 1 if attempt else 0, error, pid, plate_id),
            )

    async def _fail_start(self, pid: str, plate_id: str, reason: str) -> None:
        self.log.error("plate %s on %s: start failed: %s", plate_id, pid, reason)
        await self._start_status(pid, plate_id, "failed", error=reason[:1000])
        async with self.db.tx() as conn:
            await self._finish_failed(conn, pid, plate_id, reason)
        self.relay.wakeup.set()

    async def _finish_failed(self, conn, pid: str, plate_id: str, reason: str, cause: Envelope | None = None) -> None:
        """Release the plate so the Fleet Manager can reschedule (idempotent with the printer's own finish)."""
        cur = await conn.execute(
            "INSERT INTO telemetry.print_sessions (printer_id, plate_id, finished_at, succeeded) "
            "VALUES (%s, %s, now(), false) ON CONFLICT DO NOTHING",
            (pid, plate_id),
        )
        if cur.rowcount:
            await self.emit(
                conn, PrintFinished(printer_id=pid, plate_id=plate_id, succeeded=False, progress=0.0, reason=reason[:500]),
                cause=cause, correlationid=plate_id, id=deterministic_id(pid, plate_id, "finished"),
            )

    # ------------------------------------------------------------ operator commands

    def _adapter_for(self, printer_id: str) -> PrinterAdapter | None:
        adapter = self.adapters.get(printer_id)
        if adapter is None:
            self.log.warning("command for unknown printer %s ignored", printer_id)  # retrying would not help
        return adapter

    @on("cmd.telemetry.pause_print")
    async def pause_print(self, env, cmd: PausePrint, conn):
        if self._adapter_for(cmd.printer_id):
            self.log.warning("pause %s requested by %s: %s", cmd.printer_id, cmd.requested_by or "?", cmd.reason)
            self.pause_verified(cmd.printer_id, reason=cmd.reason, requested_by=cmd.requested_by or "unknown")

    @on("cmd.telemetry.resume_print")
    async def resume_print(self, env, cmd: ResumePrint, conn):
        if adapter := self._adapter_for(cmd.printer_id):
            self.log.info("resume %s requested by %s", cmd.printer_id, cmd.requested_by or "?")
            await adapter.resume()

    @on("cmd.telemetry.cancel_print")
    async def cancel_print(self, env, cmd: CancelPrint, conn):
        if adapter := self._adapter_for(cmd.printer_id):
            self.log.warning("cancel %s requested by %s: %s", cmd.printer_id, cmd.requested_by or "?", cmd.reason)
            await adapter.cancel()

    # ------------------------------------------------------------ calibration

    def _busy_reason(self, pid: str) -> str | None:
        if pid in self._calibrating:
            return "another calibration is running"
        if pid in self._pausing:
            return "a pause is being verified"
        if any(p == pid for p, _ in self._starting):
            return "a plate is being started"
        return None

    @on("cmd.telemetry.calibrate")
    async def calibrate(self, env, cmd: CalibratePrinter, conn):
        adapter = self.adapters.get(cmd.printer_id)
        who = cmd.requested_by or "unknown"
        cur = await conn.execute(
            "INSERT INTO telemetry.calibrations (calibration_id, printer_id, routine, temperature, requested_by, status) "
            "VALUES (%s, %s, %s, %s, %s, 'running') ON CONFLICT DO NOTHING",
            (env.id, cmd.printer_id, cmd.routine, cmd.temperature, who),
        )
        if not cur.rowcount:
            return  # redelivery: this request was already taken
        refusal = calibration_refusal(cmd, vendor=adapter.vendor if adapter else None,
                                      online=bool(adapter and adapter.online), state=self.last.get(cmd.printer_id),
                                      busy=self._busy_reason(cmd.printer_id))
        if refusal:
            self.log.warning("calibration %s on %s refused: %s", cmd.routine, cmd.printer_id, refusal)
            await conn.execute(
                "UPDATE telemetry.calibrations SET status = 'refused', detail = %s, finished_at = now() "
                "WHERE calibration_id = %s", (refusal, env.id))
            await self.emit(conn, CalibrationFinished(printer_id=cmd.printer_id, calibration_id=env.id,
                                                      routine=cmd.routine, succeeded=False, started=False,
                                                      detail=f"refused: {refusal}"), cause=env)
            return
        await self.emit(conn, CalibrationStarted(printer_id=cmd.printer_id, calibration_id=env.id,
                                                 routine=cmd.routine, requested_by=who), cause=env)
        self.log.warning("calibration %s on %s requested by %s", cmd.routine, cmd.printer_id, who)
        self.after_commit(lambda: self._begin_calibration(cmd, env.id))

    def _begin_calibration(self, cmd: CalibratePrinter, calibration_id: str) -> None:
        # Reserved only once committed (a rolled-back request must not block the printer). Handlers run one at a
        # time per consumer and this runs before the next message, so a second request still sees it.
        self._calibrating[cmd.printer_id] = calibration_id
        self.spawn(self._run_calibration(cmd, calibration_id))

    async def _run_calibration(self, cmd: CalibratePrinter, calibration_id: str) -> None:
        routine, adapter = ROUTINES[cmd.routine], self.adapters[cmd.printer_id]
        started = time.monotonic()
        ok, detail = False, ""
        try:
            await adapter.calibrate(routine, target_temperature(routine, cmd.temperature))
            ok, detail = True, f"{routine.label} finished"
        except asyncio.CancelledError:
            raise
        except CalibrationError as exc:
            detail = str(exc)
        except Exception as exc:  # noqa: BLE001 - connection drops etc.: report, never leave it "running"
            self.log.exception("calibration %s on %s failed", cmd.routine, cmd.printer_id)
            detail = f"{type(exc).__name__}: {exc}"
        finally:
            self._calibrating.pop(cmd.printer_id, None)
        seconds = round(time.monotonic() - started, 1)
        (self.log.info if ok else self.log.error)("calibration %s on %s: %s (%.0f s)", cmd.routine, cmd.printer_id,
                                                  detail, seconds)
        await self._finish_calibration(cmd.printer_id, calibration_id, cmd.routine, ok, detail, seconds)

    async def _finish_calibration(self, pid: str, calibration_id: str, routine: str, ok: bool, detail: str,
                                  seconds: float) -> None:
        async with self.db.tx() as conn:
            cur = await conn.execute(
                "UPDATE telemetry.calibrations SET status = %s, detail = %s, finished_at = now() "
                "WHERE calibration_id = %s AND status = 'running'", ("done" if ok else "failed", detail[:1000],
                                                                    calibration_id))
            if cur.rowcount:
                await self.emit(conn, CalibrationFinished(printer_id=pid, calibration_id=calibration_id,
                                                          routine=routine, succeeded=ok, detail=detail[:500],
                                                          seconds=seconds))
        self.relay.wakeup.set()

    async def _close_interrupted_calibrations(self) -> None:
        """A calibration cannot be resumed after a restart: report it, so the fleet takes the printer back."""
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT calibration_id, printer_id, routine FROM telemetry.calibrations WHERE status = 'running'"
            )).fetchall()
        for r in rows:
            await self._finish_calibration(r["printer_id"], r["calibration_id"], r["routine"], False,
                                           "the telemetry agent restarted during the calibration: check the printer",
                                           0.0)

    @on("cmd.telemetry.pause_all")
    async def pause_all(self, env, cmd: PauseAll, conn):
        printing = [pid for pid, s in self.last.items() if s.status == PrinterStatus.PRINTING]
        self.log.warning("PAUSE ALL requested by %s (%s): %s", cmd.requested_by or "?", cmd.reason, printing or "none printing")
        for pid in printing:  # each one is verified on its own; an unreachable printer raises its own alert
            self.pause_verified(pid, reason=cmd.reason or "pause all", requested_by=cmd.requested_by or "unknown")
