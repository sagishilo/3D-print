"""Printer Telemetry Agent: the only component that talks to printers.

Hot path (per telemetry sample, no DB, no LLM):
    adapter -> canonical PrinterState -> detectors -> CRITICAL? pause printer NOW
Warm path (only on change):
    lifecycle transitions + failures -> Postgres + outbox -> EVENTS stream
Live view (throttled to 1 Hz per printer):
    PrinterState -> TELEMETRY stream (tlm.<printer>.state) + KV printer_status
"""

from __future__ import annotations

import time

from printfarm.agents.base import Agent, on
from printfarm.agents.telemetry.adapters import PrinterAdapter, make_adapter
from printfarm.agents.telemetry.detectors import DetectorSet, Finding
from printfarm.contracts import deterministic_id, new_message
from printfarm.contracts.messages import (
    FailureDetected,
    PausePrint,
    PrinterConnectivity,
    PrinterState,
    PrinterStatus,
    PrintFinished,
    PrintStarted,
    Severity,
    StartPrint,
)

ACTIVE = {PrinterStatus.PRINTING, PrinterStatus.PAUSED}
DONE = {PrinterStatus.FINISHED, PrinterStatus.FAILED}
PUBLISH_EVERY_S = 1.0


class TelemetryAgent(Agent):
    name = "telemetry"

    async def on_start(self) -> None:
        self.adapters: dict[str, PrinterAdapter] = {}
        self.detectors: dict[str, DetectorSet] = {}
        self.last: dict[str, PrinterState] = {}
        self._last_pub: dict[str, float] = {}
        for cfg in self.fleet.printers:
            pid = cfg.id
            self.detectors[pid] = DetectorSet()
            self.adapters[pid] = make_adapter(
                cfg,
                on_state=lambda s, pid=pid: self._on_state(pid, s),
                on_connection=lambda up, pid=pid: self._on_connection(pid, up),
            )
            self.spawn(self.adapters[pid].run())

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
            try:
                await self.adapters[pid].pause()
                action = "paused"
            except Exception as exc:  # noqa: BLE001
                action = f"pause_failed: {exc!r}"[:200]
        self.log.error("printer %s %s: %s -> %s", pid, f.detector, f.message, action)
        async with self.db.tx() as conn:
            await conn.execute(
                "INSERT INTO telemetry.failures (printer_id, plate_id, detector, severity, message, action_taken) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (pid, s.plate_id, f.detector, f.severity, f.message, action),
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

    # ------------------------------------------------------------ commands

    @on("cmd.telemetry.start_print")
    async def start_print(self, env, cmd: StartPrint, conn):
        adapter = self.adapters.get(cmd.printer_id)
        cur = self.last.get(cmd.printer_id)
        if cur and cur.plate_id == cmd.plate_id and cur.status in ACTIVE | DONE:
            return  # already started (redelivery) - idempotent at the device
        if adapter is None or not adapter.online:
            reason = "unknown printer" if adapter is None else "printer offline"
            # Release the reservation so Fleet can reschedule; outer tx is the handler's.
            await conn.execute(
                "INSERT INTO telemetry.print_sessions (printer_id, plate_id, finished_at, succeeded) "
                "VALUES (%s, %s, now(), false) ON CONFLICT DO NOTHING",
                (cmd.printer_id, cmd.plate_id),
            )
            await self.emit(
                conn,
                PrintFinished(printer_id=cmd.printer_id, plate_id=cmd.plate_id, succeeded=False, progress=0.0, reason=reason),
                cause=env, id=deterministic_id(cmd.printer_id, cmd.plate_id, "finished"),
            )
            return
        await adapter.start_print(cmd.plate_id, cmd.artifact_uris)

    @on("cmd.telemetry.pause_print")
    async def pause_print(self, env, cmd: PausePrint, conn):
        await self.adapters[cmd.printer_id].pause()
