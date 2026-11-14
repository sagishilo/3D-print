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
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.telemetry.adapters import PrinterAdapter, make_adapter
from printfarm.agents.telemetry.detectors import DetectorSet, Finding
from printfarm.agents.telemetry.platefiles import PlateFileError, resolve_plate_file
from printfarm.agents.telemetry.safety import PauseOutcome, pause_and_verify
from printfarm.agents.telemetry.transfer import PlateFile, UploadError
from printfarm.contracts import Envelope, deterministic_id, new_message
from printfarm.contracts.messages import (
    CancelPrint,
    FailureDetected,
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


class StartFailed(RuntimeError):
    """The printer did not take the plate; the message is shown to the operator."""


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
        if f.severity == Severity.CRITICAL and s.status == PrinterStatus.PRINTING:
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

    async def _lifecycle_started(self, pid: str, plate_id: str) -> None:
        async with self.db.tx() as conn:
            cur = await conn.execute(
                "INSERT INTO telemetry.print_sessions (printer_id, plate_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (pid, plate_id),
            )
            if cur.rowcount:
                await self.emit(conn, PrintStarted(printer_id=pid, plate_id=plate_id), correlationid=plate_id,
                                id=deterministic_id(pid, plate_id, "started"))
        self.relay.wakeup.set()

    async def _lifecycle_finished(self, pid: str, plate_id: str, ok: bool, progress: float, reason: str) -> None:
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
                    PrintFinished(printer_id=pid, plate_id=plate_id, succeeded=ok, progress=progress, reason=reason),
                    correlationid=plate_id, id=deterministic_id(pid, plate_id, "finished"),
                )
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
        if adapter is None or not adapter.online:
            await self._finish_failed(conn, cmd.printer_id, cmd.plate_id,
                                      "unknown printer" if adapter is None else "printer offline", cause=env)
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

    @on("cmd.telemetry.pause_all")
    async def pause_all(self, env, cmd: PauseAll, conn):
        printing = [pid for pid, s in self.last.items() if s.status == PrinterStatus.PRINTING]
        self.log.warning("PAUSE ALL requested by %s (%s): %s", cmd.requested_by or "?", cmd.reason, printing or "none printing")
        for pid in printing:  # each one is verified on its own; an unreachable printer raises its own alert
            self.pause_verified(pid, reason=cmd.reason or "pause all", requested_by=cmd.requested_by or "unknown")
