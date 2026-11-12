"""Simulated printers speaking the real wire protocols.

* Bambu: connects to an MQTT broker as the "printer", publishes
  ``device/<serial>/report`` and obeys ``device/<serial>/request``.
  (A real Bambu printer *is* the broker; in dev Mosquitto stands in for it.)
* Moonraker: serves JSON-RPC over WebSocket at ``ws://0.0.0.0:<port>/websocket``.

Fault injection (``SIM_FAULTS="bambu-01=thermal_runaway,voron-01=stall"``):
    thermal_runaway  nozzle temperature climbs uncontrolled after 30 % progress
    stall            progress freezes at 50 %
    fail             the printer reports a failure at 60 %
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import aiomqtt
import websockets

from printfarm.config import FleetCfg, PrinterCfg

log = logging.getLogger("simulator")

TICK_S = 0.5


@dataclass
class SimPrinter:
    id: str
    print_seconds: float
    fault: str | None = None
    status: str = "idle"          # idle | printing | paused | finished | failed
    plate: str = ""
    progress: float = 0.0
    nozzle: float = 25.0
    nozzle_target: float = 0.0
    bed: float = 25.0
    bed_target: float = 0.0
    total_layers: int = 60
    error: int = 0

    def start(self, plate: str) -> None:
        if self.status in ("printing", "paused") and self.plate == plate:
            return
        if self.status in ("printing", "paused"):
            log.warning("%s busy, ignoring start of %s", self.id, plate)
            return
        self.status, self.plate, self.progress, self.error = "printing", plate, 0.0, 0
        self.nozzle_target, self.bed_target = 220.0, 60.0
        log.info("%s START %s", self.id, plate)

    def pause(self) -> None:
        if self.status == "printing":
            self.status = "paused"
            log.warning("%s PAUSED", self.id)

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "printing"

    def cancel(self) -> None:
        if self.status in ("printing", "paused"):
            self.status, self.nozzle_target, self.bed_target = "failed", 0.0, 0.0

    def tick(self, dt: float) -> None:
        runaway = self.fault == "thermal_runaway" and self.status == "printing" and self.progress >= 0.3
        if runaway:
            self.nozzle += 4.0 * dt
        else:
            self.nozzle += ((self.nozzle_target or 25.0) - self.nozzle) * min(1.0, dt / 1.5)
        self.bed += ((self.bed_target or 25.0) - self.bed) * min(1.0, dt / 2.0)
        self.nozzle += random.uniform(-0.3, 0.3)
        self.bed += random.uniform(-0.2, 0.2)

        if self.status != "printing":
            return
        heated = abs(self.nozzle - self.nozzle_target) < 5 and abs(self.bed - self.bed_target) < 5
        if not heated:  # includes a runaway: the print effectively stops progressing
            return
        if self.fault == "stall" and self.progress >= 0.5:
            return
        if self.fault == "fail" and self.progress >= 0.6:
            self.status, self.error, self.nozzle_target, self.bed_target = "failed", 50348044, 0.0, 0.0
            return
        self.progress = min(1.0, self.progress + dt / self.print_seconds)
        if self.progress >= 1.0:
            self.status, self.nozzle_target, self.bed_target = "finished", 0.0, 0.0
            log.info("%s FINISHED %s", self.id, self.plate)

    @property
    def layer(self) -> int:
        return int(self.progress * self.total_layers)


# ---------------------------------------------------------------- Bambu over MQTT

BAMBU_STATE = {"idle": "IDLE", "printing": "RUNNING", "paused": "PAUSE", "finished": "FINISH", "failed": "FAILED"}


def bambu_report(p: SimPrinter) -> dict:
    return {
        "print": {
            "command": "push_status",
            "gcode_state": BAMBU_STATE[p.status],
            "mc_percent": int(p.progress * 100),
            "mc_remaining_time": int((1 - p.progress) * p.print_seconds / 60),
            "nozzle_temper": round(p.nozzle, 1), "nozzle_target_temper": p.nozzle_target,
            "bed_temper": round(p.bed, 1), "bed_target_temper": p.bed_target,
            "layer_num": p.layer, "total_layer_num": p.total_layers,
            "subtask_name": p.plate, "print_error": p.error,
        }
    }


async def run_bambu(p: SimPrinter, cfg: PrinterCfg) -> None:
    report, request = f"device/{cfg.serial}/report", f"device/{cfg.serial}/request"
    while True:
        try:
            async with aiomqtt.Client(cfg.host, cfg.port, identifier=f"sim-{cfg.id}") as client:
                await client.subscribe(request)
                log.info("bambu sim %s connected to broker %s:%s", cfg.id, cfg.host, cfg.port)

                async def commands():
                    async for m in client.messages:
                        body = json.loads(m.payload).get("print", {})
                        match body.get("command"):
                            case "project_file":
                                p.start(body.get("subtask_name", ""))
                            case "pause":
                                p.pause()
                            case "resume":
                                p.resume()
                            case "stop":
                                p.cancel()
                        await client.publish(report, json.dumps(bambu_report(p)))

                cmd_task = asyncio.create_task(commands())
                try:
                    while True:
                        p.tick(TICK_S)
                        await client.publish(report, json.dumps(bambu_report(p)))
                        await asyncio.sleep(TICK_S)
                finally:
                    cmd_task.cancel()
        except aiomqtt.MqttError as exc:
            log.warning("bambu sim %s: %r - retrying", cfg.id, exc)
            await asyncio.sleep(2)


# ---------------------------------------------------------------- Moonraker over WebSocket

MOONRAKER_STATE = {"idle": "standby", "printing": "printing", "paused": "paused", "finished": "complete", "failed": "error"}


def moonraker_status(p: SimPrinter) -> dict:
    return {
        "print_stats": {
            "state": MOONRAKER_STATE[p.status],
            "filename": f"{p.plate}.gcode" if p.plate else "",
            "message": f"error {p.error}" if p.error else "",
            "info": {"current_layer": p.layer, "total_layer": p.total_layers},
        },
        "extruder": {"temperature": round(p.nozzle, 1), "target": p.nozzle_target},
        "heater_bed": {"temperature": round(p.bed, 1), "target": p.bed_target},
        "display_status": {"progress": round(p.progress, 4)},
    }


async def run_moonraker(p: SimPrinter, cfg: PrinterCfg) -> None:
    port = urlparse(cfg.url).port or 7125

    async def handle(ws):
        async def push():
            while True:
                await asyncio.sleep(TICK_S)
                await ws.send(json.dumps({"jsonrpc": "2.0", "method": "notify_status_update",
                                          "params": [moonraker_status(p), time.time()]}))

        pusher = None
        try:
            async for raw in ws:
                req = json.loads(raw)
                method, params, rid = req.get("method"), req.get("params") or {}, req.get("id")
                result: object = "ok"
                match method:
                    case "server.connection.identify":
                        result = {"connection_id": id(ws)}
                    case "printer.objects.subscribe":
                        result = {"eventtime": time.time(), "status": moonraker_status(p)}
                        pusher = pusher or asyncio.create_task(push())
                    case "printer.print.start":
                        p.start(params.get("filename", "").removesuffix(".gcode"))
                    case "printer.print.pause":
                        p.pause()
                    case "printer.print.resume":
                        p.resume()
                    case "printer.print.cancel":
                        p.cancel()
                    case _:
                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid,
                                                  "error": {"code": -32601, "message": "Method not found"}}))
                        continue
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}))
        except websockets.ConnectionClosed:
            pass
        finally:
            if pusher:
                pusher.cancel()

    async def physics():
        while True:
            p.tick(TICK_S)
            await asyncio.sleep(TICK_S)

    async with websockets.serve(handle, "0.0.0.0", port):
        log.info("moonraker sim %s listening on :%d", cfg.id, port)
        await physics()


# ---------------------------------------------------------------- entry point


def parse_faults(spec: str) -> dict[str, str]:
    out = {}
    for part in filter(None, (s.strip() for s in spec.split(","))):
        pid, _, fault = part.partition("=")
        out[pid] = fault
    return out


async def run_simulator(fleet: FleetCfg, faults: dict[str, str] | None = None) -> None:
    faults = faults if faults is not None else parse_faults(os.getenv("SIM_FAULTS", ""))
    tasks = []
    for cfg in fleet.printers:
        p = SimPrinter(cfg.id, fleet.sim_print_seconds, faults.get(cfg.id))
        runner = run_bambu if cfg.vendor == "bambu" else run_moonraker
        tasks.append(asyncio.create_task(runner(p, cfg)))
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
