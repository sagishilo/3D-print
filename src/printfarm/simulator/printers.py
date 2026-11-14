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
    calibration_fail  calibrations end with an error (Bambu: failed state; Klipper: an error reply)
    hms_warning       the printer reports a health (HMS) warning code
    camera_off | camera_auth_fail | camera_drop | camera_stall   see camera.py

Hardware readings (fans, chamber, speed, Wi-Fi, firmware, AMS, Klipper host stats) follow the printer's activity,
and calibrations take a few seconds of simulated time (``CALIBRATION_S``).
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
                "calibration_fail", "hms_warning", "camera_off", "camera_auth_fail", "camera_drop", "camera_stall"}
# Simulated duration and Bambu stage (stg_cur) of each routine.
CALIBRATION_S = {"home": 3.0, "bed_level": 8.0, "vibration": 8.0, "motor_noise": 6.0, "nozzle_pid": 8.0,
                 "bed_pid": 8.0}
CALIBRATION_STAGE = {"home": 13, "bed_level": 1, "vibration": 3, "motor_noise": 25, "nozzle_pid": 7, "bed_pid": 2}
ERR_CALIBRATION = 0x0300_4001
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
    model: str = ""
    material: str = "PLA"
    color: str = "black"
    # hardware
    chamber: float = 26.0
    wifi_dbm: int = -52
    speed_level: int = 2                                        # Bambu: 1 silent, 2 standard, 3 sport, 4 ludicrous
    homed: str = ""                                             # Klipper toolhead.homed_axes
    mesh: str = ""                                              # Klipper bed_mesh.profile_name
    calibration: str = ""                                       # routine running ("" = none)
    calibration_left: float = 0.0
    calibration_error: str = ""                                 # Klipper: error of the last script

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

    # ------------------------------------------------------------ calibration

    def start_calibration(self, routine: str, *, target: float | None = None) -> str | None:
        """Begin ``routine``; returns why it cannot run (busy) or None."""
        if self.status in ("printing", "paused") or self.calibration:
            return f"printer busy ({self.calibration or self.status})"
        self.status, self.plate, self.progress, self.error = "calibrating", "", 0.0, 0
        self.calibration, self.calibration_left, self.calibration_error = routine, CALIBRATION_S.get(routine, 5.0), ""
        if routine == "nozzle_pid":
            self.nozzle_target = target or 220.0
        if routine == "bed_pid":
            self.bed_target = target or 60.0
        log.info("%s CALIBRATION %s", self.id, routine)
        return None

    def _end_calibration(self) -> None:
        routine = self.calibration
        self.calibration, self.nozzle_target, self.bed_target = "", 0.0, 0.0
        if self.has("calibration_fail"):
            self.status, self.error = "failed", ERR_CALIBRATION
            self.calibration_error = f"{routine}: probe did not trigger"
            log.warning("%s calibration %s FAILED (simulated fault)", self.id, routine)
            return
        self.status = "idle"
        self.homed = "xyz"
        if routine == "bed_level":
            self.mesh = "default"
        log.info("%s calibration %s done", self.id, routine)

    def emergency_stop(self) -> None:
        self.calibration, self.calibration_left, self.calibration_error = "", 0.0, "Emergency stop"
        self.status, self.error, self.nozzle_target, self.bed_target, self.homed = "failed", 1, 0.0, 0.0, ""
        log.warning("%s EMERGENCY STOP", self.id)

    @property
    def fans(self) -> dict[str, int]:
        """Fan speeds in percent, following what the printer is doing."""
        printing = self.status == "printing" and self.layer >= 1
        return {"part": 100 if printing else 0, "aux": 40 if printing else 0,
                "chamber": 30 if printing and self.model.startswith("x1") else 0,
                "hotend": 100 if self.nozzle > 50 else 0}

    @property
    def stage(self) -> int:
        if self.calibration:
            return CALIBRATION_STAGE.get(self.calibration, 0)
        return 0 if self.status == "printing" else 255

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
        self.chamber += (26.0 + max(self.bed - 25.0, 0.0) * 0.2 - self.chamber) * min(1.0, dt / 30.0)
        self.wifi_dbm = max(-80, min(-35, self.wifi_dbm + random.choice((-1, 0, 0, 1))))

        if self.calibration:
            self.calibration_left -= dt
            if self.calibration_left <= 0:
                self._end_calibration()
            return
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

BAMBU_STATE = {"idle": "IDLE", "printing": "RUNNING", "paused": "PAUSE", "finished": "FINISH", "failed": "FAILED",
               "calibrating": "RUNNING"}
BAMBU_FIRMWARE = {"x1": "01.08.05.00", "p1": "01.07.00.00", "a1": "01.04.00.00"}
SPEED_MAG = {1: 50, 2: 100, 3: 124, 4: 166}
AMS_SLOTS = (("PLA", "000000FF"), ("PLA", "FFFFFFFF"), ("PETG", "1F6FEBFF"), ("PLA", "E3A008FF"))


def bambu_ams(p: SimPrinter) -> dict:
    """One AMS unit; slot 0 holds the printer's configured filament, the rest a fixed set."""
    first = (p.material, {"black": "000000FF", "white": "FFFFFFFF"}.get(p.color, "808080FF"))
    trays = []
    for slot, (material, color) in enumerate((first, *AMS_SLOTS[1:])):
        remain = max(0, 80 - slot * 23 - int(p.progress * 5)) if slot else max(0, 65 - int(p.progress * 10))
        trays.append({"id": str(slot), "tray_type": material, "tray_color": color, "remain": remain})
    return {"ams": [{"id": "0", "humidity": "4", "temp": f"{p.chamber - 1:.1f}", "tray": trays}]}


def bambu_report(p: SimPrinter) -> dict:
    fans = p.fans
    return {
        "print": {
            "stg_cur": p.stage,
            "cooling_fan_speed": str(round(fans["part"] / 100 * 15)),
            "big_fan1_speed": str(round(fans["aux"] / 100 * 15)),
            "big_fan2_speed": str(round(fans["chamber"] / 100 * 15)),
            "heatbreak_fan_speed": str(round(fans["hotend"] / 100 * 15)),
            "chamber_temper": round(p.chamber, 1),
            "spd_lvl": p.speed_level, "spd_mag": SPEED_MAG[p.speed_level],
            "wifi_signal": f"{p.wifi_dbm}dBm",
            "nozzle_diameter": "0.4", "nozzle_type": "hardened_steel" if p.model.startswith("x1") else "stainless_steel",
            "hms": [{"attr": 0x0300_0100, "code": 0x0001_0007}] if p.has("hms_warning") else [],
            "ams": bambu_ams(p),
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
            if p.calibration:
                p.emergency_stop()
            else:
                p.cancel()
        case "calibration":
            option = int(body.get("option") or 0)
            routines = [r for bit, r in ((1, "bed_level"), (2, "vibration"), (3, "motor_noise")) if option & (1 << bit)]
            if not routines:
                return False, "no calibration selected"
            err = p.start_calibration(routines[0])
            if err:
                return False, err
            p.calibration_left = sum(CALIBRATION_S[r] for r in routines)
        case "gcode_line":
            if str(body.get("param", "")).strip().upper().startswith("G28"):
                err = p.start_calibration("home")
                if err:
                    return False, err
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
                        if (msg.get("info") or {}).get("command") == "get_version":
                            sw = BAMBU_FIRMWARE.get(cfg.model[:2], "01.00.00.00")
                            await client.publish(report, json.dumps({"info": {
                                "command": "get_version", "sequence_id": msg["info"].get("sequence_id", "0"),
                                "module": [{"name": "ota", "sw_ver": sw}, {"name": "mc", "sw_ver": "00.00.30.00"}]}}))
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
        p = SimPrinter(cfg.id, fleet.sim_print_seconds, parsed.get(cfg.id, frozenset()), cfg.access_code(),
                       model=cfg.model.lower(), material=cfg.material, color=cfg.color)
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
