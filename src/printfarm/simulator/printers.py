"""Simulated printers speaking the real wire protocols.

* Bambu: connects to an MQTT broker as the "printer", publishes ``device/<serial>/report`` and obeys
  ``device/<serial>/request`` (a real Bambu printer *is* the broker; in dev Mosquitto stands in for it). It answers
  every command with ``result``/``reason`` like the firmware, serves implicit FTPS for plate uploads (``ftps.py``)
  and the P1/A1 port-6000 camera (``camera.py``). ``project_file`` only starts a file that was uploaded, and checks
  its md5.
* Moonraker: HTTP API + JSON-RPC WebSocket on one port, with file upload and a Crowsnest webcam (``moonraker.py``).

Fault injection: ``SIM_FAULTS="bambu-01=thermal_runaway,voron-01=stall+camera_drop"`` (``+`` combines faults).
    thermal_runaway   nozzle temperature climbs uncontrolled after 30 % progress
    stall             progress freezes at 50 %
    fail              the printer reports a failure at 60 %
    ignore_commands   every control command is dropped (like firmware that refuses third-party control)
    ignore_pause      pause is dropped, everything else works
    upload_fail       uploads are refused (no space)
    upload_flaky      the first upload of each file fails with a temporary error
    camera_off | camera_auth_fail | camera_drop | camera_stall   see camera.py
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import random
from dataclasses import dataclass, field
from urllib.parse import urlparse

import aiomqtt

from printfarm.config import FleetCfg, PrinterCfg
from printfarm.simulator.assets import server_tls_context
from printfarm.simulator.camera import BambuCameraServer
from printfarm.simulator.ftps import SimFtpsServer
from printfarm.simulator.moonraker import run_moonraker

log = logging.getLogger("simulator")

TICK_S = 0.5
KNOWN_FAULTS = {"thermal_runaway", "stall", "fail", "ignore_commands", "ignore_pause", "upload_fail", "upload_flaky",
                "camera_off", "camera_auth_fail", "camera_drop", "camera_stall"}
ERR_FILE_NOT_FOUND = 0x0500_C010   # simulated firmware error codes (not Bambu's real numbering)
ERR_MD5_MISMATCH = 0x0500_C011
ERR_PRINT_FAILED = 50348044


@dataclass
class SimPrinter:
    id: str
    print_seconds: float
    faults: frozenset[str] = frozenset()
    access_code: str = ""
    status: str = "idle"          # idle | printing | paused | finished | failed
    plate: str = ""
    progress: float = 0.0
    nozzle: float = 25.0
    nozzle_target: float = 0.0
    bed: float = 25.0
    bed_target: float = 0.0
    total_layers: int = 60
    error: int = 0
    files: dict[str, bytes] = field(default_factory=dict)       # the printer's storage (uploads)
    flaky_failed: set[str] = field(default_factory=set)         # upload_flaky: files that already failed once

    def has(self, fault: str) -> bool:
        return fault in self.faults

    def start(self, plate: str) -> None:
        if self.status in ("printing", "paused") and self.plate == plate:
            return
        if self.status in ("printing", "paused"):
            log.warning("%s busy, ignoring start of %s", self.id, plate)
            return
        self.status, self.plate, self.progress, self.error = "printing", plate, 0.0, 0
        self.nozzle_target, self.bed_target = 220.0, 60.0
        log.info("%s START %s", self.id, plate)

    def start_file(self, name: str, *, md5: str | None, plate: str) -> str | None:
        """Start an uploaded file. Returns an error text (and reports a failed plate) if it can't."""
        data = self.files.get(name)
        if data is None:
            self._refuse(plate, ERR_FILE_NOT_FOUND)
            return f"file {name} not found on the printer"
        if md5 and hashlib.md5(data).hexdigest() != md5.lower():  # noqa: S324 - integrity check, as the firmware does
            self._refuse(plate, ERR_MD5_MISMATCH)
            return f"md5 of {name} does not match"
        self.start(plate)
        return None

    def _refuse(self, plate: str, code: int) -> None:
        if self.status not in ("printing", "paused"):
            self.status, self.plate, self.progress, self.error = "failed", plate, 0.0, code

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
        runaway = self.has("thermal_runaway") and self.status == "printing" and self.progress >= 0.3
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
        if self.has("stall") and self.progress >= 0.5:
            return
        if self.has("fail") and self.progress >= 0.6:
            self.status, self.error, self.nozzle_target, self.bed_target = "failed", ERR_PRINT_FAILED, 0.0, 0.0
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


def file_from_url(url: str) -> str:
    """'ftp:///plate.3mf', 'ftp:///cache/plate.3mf', 'file:///sdcard/plate.3mf' -> 'plate.3mf'."""
    return urlparse(url).path.rstrip("/").split("/")[-1]


def handle_bambu_command(p: SimPrinter, body: dict) -> tuple[bool, str] | None:
    """Apply one ``print`` command. Returns (ok, reason) for the reply, or None to drop it silently."""
    cmd = body.get("command")
    if p.has("ignore_commands") or (cmd == "pause" and p.has("ignore_pause")):
        log.warning("%s: ignoring %s (simulated fault)", p.id, cmd)
        return None
    match cmd:
        case "project_file":
            name = file_from_url(str(body.get("url", ""))) or str(body.get("file", ""))
            err = p.start_file(name, md5=body.get("md5") or None, plate=str(body.get("subtask_name") or name))
            return (False, err) if err else (True, "")
        case "pause":
            p.pause()
        case "resume":
            p.resume()
        case "stop":
            p.cancel()
        case _:
            return False, f"unsupported command {cmd!r}"
    return True, ""


async def run_bambu(p: SimPrinter, cfg: PrinterCfg) -> None:
    report, request = f"device/{cfg.serial}/report", f"device/{cfg.serial}/request"
    while True:
        try:
            async with aiomqtt.Client(cfg.host, cfg.port, identifier=f"sim-{cfg.id}") as client:
                await client.subscribe(request)
                log.info("bambu sim %s connected to broker %s:%s", cfg.id, cfg.host, cfg.port)

                async def commands():
                    async for m in client.messages:
                        msg = json.loads(m.payload)
                        if "pushing" in msg:  # pushall: answer with a full report
                            await client.publish(report, json.dumps(bambu_report(p)))
                            continue
                        body = msg.get("print") or {}
                        answer = handle_bambu_command(p, body)
                        if answer is not None:
                            ok, reason = answer
                            await client.publish(report, json.dumps({"print": {
                                "command": body.get("command"), "sequence_id": body.get("sequence_id", "0"),
                                "result": "success" if ok else "failed", "reason": reason}}))
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


async def run_bambu_services(p: SimPrinter, cfg: PrinterCfg, bind: str) -> None:
    """The printer's own servers: implicit FTPS (uploads) and, for P1/A1 models unless camera_off, the port-6000
    camera. X1 models stream RTSPS instead (simulated by the x1-camera-sim container in docker-compose.yml)."""
    ctx = server_tls_context()
    ftps = SimFtpsServer(p, ctx)
    await ftps.start(bind, cfg.ftp_port)
    log.info("bambu sim %s: FTPS on %s:%d", cfg.id, bind, ftps.port)
    servers = [ftps.server]
    if not p.has("camera_off") and not cfg.model.lower().startswith("x1"):
        camera = BambuCameraServer(p, server_tls_context())
        await camera.start(bind, cfg.camera_port)
        log.info("bambu sim %s: camera on %s:%d", cfg.id, bind, camera.port)
        servers.append(camera.server)
    try:
        await asyncio.Event().wait()
    finally:
        for s in servers:
            s.close()


# ---------------------------------------------------------------- entry point


def parse_faults(spec: str) -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for part in filter(None, (s.strip() for s in spec.split(","))):
        pid, _, faults = part.partition("=")
        names = frozenset(f.strip() for f in faults.split("+") if f.strip())
        unknown = names - KNOWN_FAULTS
        if unknown:
            log.warning("unknown simulator fault(s) for %s: %s", pid, ", ".join(sorted(unknown)))
        out[pid.strip()] = names
    return out


async def run_simulator(fleet: FleetCfg, faults: dict[str, str | frozenset[str]] | None = None,
                        bind: str | None = None) -> None:
    if faults is None:
        parsed = parse_faults(os.getenv("SIM_FAULTS", ""))
    else:  # tests pass {"printer": "fault+fault"} or sets
        parsed = {k: frozenset(v.split("+")) if isinstance(v, str) else frozenset(v) for k, v in faults.items()}
    bind = bind or os.getenv("SIM_BIND", "0.0.0.0")
    tasks = []
    for cfg in fleet.printers:
        p = SimPrinter(cfg.id, fleet.sim_print_seconds, parsed.get(cfg.id, frozenset()), cfg.access_code())
        if p.faults:
            log.warning("simulated printer %s faults: %s", cfg.id, ", ".join(sorted(p.faults)))
        if cfg.vendor == "bambu":
            tasks.append(asyncio.create_task(run_bambu(p, cfg)))
            tasks.append(asyncio.create_task(run_bambu_services(p, cfg, bind)))
        else:
            tasks.append(asyncio.create_task(run_moonraker(p, urlparse(cfg.url).port or 7125, bind)))
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
