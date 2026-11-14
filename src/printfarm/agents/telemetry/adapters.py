"""Vendor adapters: translate printer protocols into the canonical ``PrinterState``.

* ``BambuMqttAdapter``  - Bambu Lab LAN mode: MQTT over TLS on port 8883,
  user ``bblp``, password = the printer's LAN access code. Reports arrive on
  ``device/<serial>/report`` (often partial -> merged here), commands go to
  ``device/<serial>/request``.
* ``MoonrakerAdapter``  - Klipper/Moonraker: JSON-RPC 2.0 over WebSocket
  (``ws://host:7125/websocket``), ``printer.objects.subscribe`` then
  ``notify_status_update`` diffs.

Both reconnect forever with capped exponential backoff and report
connectivity changes. Commands are idempotent per plate id.

Calibration (``calibrate``) returns when the printer has finished the routine, judged from the printer's own
state, and raises ``CalibrationError`` when it refuses, never starts or reports an error. While it runs the
adapter reports ``PrinterStatus.CALIBRATING``.

Bambu Cloud API is intentionally not used for control: LAN mode keeps the
safety loop working when the internet is down (see ADR-0003).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import ssl
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import aiomqtt
import websockets

from printfarm.agents.telemetry.transfer import PlateFile, upload_ftps, upload_moonraker
from printfarm.config import PrinterCfg
from printfarm.contracts.calibration import Routine
from printfarm.contracts.messages import AmsTray, AmsUnit, PrinterHardware, PrinterState, PrinterStatus

log = logging.getLogger(__name__)

StateCb = Callable[[PrinterState], Awaitable[None]]
ConnCb = Callable[[bool], Awaitable[None]]


CALIBRATION_START_S = 60.0     # the printer must show that it took the routine within this time


class CalibrationError(RuntimeError):
    """The calibration did not run or did not finish; the message is shown to the operator."""


async def _poll(predicate: Callable[[], bool], timeout: float, every: float = 0.2) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(every)
    return True


def _num(value: Any, cast=float) -> Any:
    """'0.4' / 12 / '-52dBm' -> number; anything unparsable -> None."""
    if value is None:
        return None
    try:
        return cast(str(value).lower().removesuffix("dbm").strip())
    except (TypeError, ValueError):
        return None


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class PrinterAdapter(ABC):
    vendor = "generic"

    def __init__(self, cfg: PrinterCfg, on_state: StateCb, on_connection: ConnCb):
        self.cfg, self.on_state, self.on_connection = cfg, on_state, on_connection
        self.online = False
        self.raw: dict[str, Any] = {}
        self.calibrating: Routine | None = None   # set while a routine started by the agent runs
        self.firmware: str | None = None
        # States are handed to the agent through a queue, never awaited inline by the
        # protocol reader: the agent may react with a command (pause!) whose reply
        # must be read by that same reader - awaiting inline would deadlock.
        self._states: asyncio.Queue[PrinterState] = asyncio.Queue(maxsize=100)

    def publish_state(self) -> None:
        if self._states.full():  # agent is behind: drop the oldest sample, keep the newest
            self._states.get_nowait()
        self._states.put_nowait(self.to_state())

    async def _dispatch(self) -> None:
        while True:
            state = await self._states.get()
            try:
                await self.on_state(state)
            except Exception:  # noqa: BLE001 - never let one bad sample stop the stream
                log.exception("%s: state handler failed", self.cfg.id)

    async def run(self) -> None:
        dispatcher = asyncio.create_task(self._dispatch())
        try:
            await self._run()
        finally:
            dispatcher.cancel()

    async def _run(self) -> None:
        delay = 1.0
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport failure -> reconnect
                log.warning("%s: connection lost: %r", self.cfg.id, exc)
            if self.online:
                self.online = False
                await self.on_connection(False)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _connected(self) -> None:
        if not self.online:
            self.online = True
            await self.on_connection(True)

    @abstractmethod
    async def _session(self) -> None: ...

    @abstractmethod
    async def upload(self, plate: PlateFile, timeout: float) -> float:
        """Transfer the plate file to the printer; returns seconds. Raises UploadError."""

    @abstractmethod
    async def start_plate(self, plate: PlateFile) -> None:
        """Tell the printer to print an uploaded plate file (does not wait for it to start)."""

    def last_reply(self, command: str) -> str:
        """The printer's answer to the most recent ``command`` (empty when the protocol has none)."""
        return ""

    @abstractmethod
    async def pause(self) -> None: ...

    @abstractmethod
    async def resume(self) -> None: ...

    @abstractmethod
    async def cancel(self) -> None: ...

    async def calibrate(self, routine: Routine, temperature: int | None) -> None:
        """Run ``routine`` and return once the printer has finished it (raises CalibrationError)."""
        self.calibrating = routine
        self.publish_state()
        try:
            await self._calibrate(routine, temperature)
        finally:
            self.calibrating = None
            if self.online:
                self.publish_state()

    @abstractmethod
    async def _calibrate(self, routine: Routine, temperature: int | None) -> None: ...

    @abstractmethod
    async def abort(self) -> None:
        """Stop a running calibration at once (safety: a critical finding while calibrating)."""

    @abstractmethod
    def to_state(self) -> PrinterState: ...


# ---------------------------------------------------------------- Bambu Lab

BAMBU_STATUS = {
    "IDLE": PrinterStatus.IDLE,
    "PREPARE": PrinterStatus.PRINTING,
    "SLICING": PrinterStatus.PRINTING,
    "RUNNING": PrinterStatus.PRINTING,
    "PAUSE": PrinterStatus.PAUSED,
    "FINISH": PrinterStatus.FINISHED,
    "FAILED": PrinterStatus.FAILED,
}


BAMBU_BUSY = {"RUNNING", "PREPARE", "SLICING"}
BAMBU_SPEED = {1: "silent", 2: "standard", 3: "sport", 4: "ludicrous"}
BAMBU_FANS = {"part": "cooling_fan_speed", "aux": "big_fan1_speed", "chamber": "big_fan2_speed",
              "hotend": "heatbreak_fan_speed"}
# stg_cur values as documented by the community (ha-bambulab); unknown values are shown as "stage N".
BAMBU_STAGES = {
    1: "auto bed leveling", 2: "heatbed preheating", 3: "vibration compensation", 4: "changing filament",
    5: "M400 pause", 6: "paused: filament runout", 7: "heating hotend", 8: "calibrating extrusion",
    9: "scanning bed surface", 10: "inspecting first layer", 11: "identifying build plate", 12: "calibrating micro lidar",
    13: "homing toolhead", 14: "cleaning nozzle tip", 15: "checking extruder temperature", 16: "paused by user",
    17: "paused: front cover fell", 18: "calibrating micro lidar", 19: "calibrating extrusion flow",
    20: "paused: nozzle temperature malfunction", 21: "paused: heatbed temperature malfunction",
    25: "motor noise calibration",
}


def bambu_hardware(r: dict, firmware: str | None) -> PrinterHardware:
    fans = {}
    for name, key in BAMBU_FANS.items():
        level = _num(r.get(key), int)
        if level is not None:
            fans[name] = round(min(max(level, 0), 15) / 15 * 100)
    stage_no = _num(r.get("stg_cur"), int)
    stage = None if stage_no in (None, 0, -1, 255) else BAMBU_STAGES.get(stage_no, f"stage {stage_no}")
    units = []
    for u in (r.get("ams") or {}).get("ams") or []:
        unit = _num(u.get("id"), int) or 0
        trays = []
        for t in u.get("tray") or []:
            color = str(t.get("tray_color") or "")
            remain = _num(t.get("remain"), int)
            trays.append(AmsTray(unit=unit, slot=_num(t.get("id"), int) or 0, material=t.get("tray_type") or None,
                                 color=f"#{color[:6]}" if len(color) >= 6 else None,
                                 remaining_pct=remain if remain is not None and remain >= 0 else None))
        units.append(AmsUnit(unit=unit, humidity_level=_num(u.get("humidity"), int), temp=_num(u.get("temp")),
                             trays=trays))
    hms = []
    for h in r.get("hms") or []:
        attr, code = _num(h.get("attr"), int), _num(h.get("code"), int)
        if attr is not None and code is not None:
            hms.append(f"{attr >> 16:04X}_{attr & 0xFFFF:04X}_{code >> 16:04X}_{code & 0xFFFF:04X}")
    level = _num(r.get("spd_lvl"), int)
    return PrinterHardware(
        chamber_temp=_num(r.get("chamber_temper")), fans=fans,
        speed_mode=BAMBU_SPEED.get(level) if level else None, speed_pct=_num(r.get("spd_mag"), int),
        nozzle_diameter=_num(r.get("nozzle_diameter")), nozzle_type=r.get("nozzle_type") or None,
        wifi_dbm=_num(r.get("wifi_signal"), int), firmware=firmware, stage=stage, ams=units, hms=hms,
    )


class BambuMqttAdapter(PrinterAdapter):
    vendor = "bambu"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._client: aiomqtt.Client | None = None
        self._seq = itertools.count(1)
        self._replies: dict[str, str] = {}   # command -> "success" | "failed: <reason>"

    def _tls(self) -> ssl.SSLContext | None:
        if not self.cfg.tls:
            return None
        ctx = ssl.create_default_context()
        # Bambu printers present a self-signed certificate. Pin it in production
        # (ctx.load_verify_locations(<printer CA>)); here we only disable hostname checks.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _session(self) -> None:
        code = self.cfg.access_code()
        async with aiomqtt.Client(
            self.cfg.host, self.cfg.port,
            username="bblp" if code else None, password=code or None,
            tls_context=self._tls(), identifier=f"printfarm-{self.cfg.id}", keepalive=15,
        ) as client:
            self._client = client
            await client.subscribe(f"device/{self.cfg.serial}/report")
            # Once per connection only: P1-series printers lag if asked for a full report more often than ~5 min.
            await self._request({"pushing": {"command": "pushall", "version": 1, "push_target": 1}})
            await self._request({"info": {"command": "get_version"}})
            await self._connected()
            async for message in client.messages:
                try:
                    data = json.loads(message.payload)
                except ValueError:
                    continue
                info = data.get("info")
                if isinstance(info, dict) and info.get("command") == "get_version":
                    ota = next((m for m in info.get("module") or [] if m.get("name") == "ota"), None)
                    self.firmware = (ota or {}).get("sw_ver") or self.firmware
                    continue
                body = data.get("print")
                if not isinstance(body, dict):
                    continue
                if "result" in body and body.get("command") not in (None, "push_status"):
                    # The printer's answer to one of our commands (it echoes command + sequence_id).
                    result = str(body["result"]).lower()
                    self._replies[body["command"]] = result if result == "success" else f"{result}: {body.get('reason', '')}"
                    continue
                _deep_merge(self.raw, body)
                self.publish_state()
        self._client = None

    async def _request(self, body: dict) -> None:
        if self._client is None:
            raise ConnectionError(f"{self.cfg.id} not connected")
        for v in body.values():
            v.setdefault("sequence_id", str(next(self._seq)))
            if v.get("command") in self._replies:
                del self._replies[v["command"]]  # a reply from now on belongs to this request
        await self._client.publish(f"device/{self.cfg.serial}/request", json.dumps(body), qos=1)

    def last_reply(self, command: str) -> str:
        return self._replies.get(command, "")

    async def upload(self, plate: PlateFile, timeout: float) -> float:
        return await upload_ftps(plate, host=self.cfg.ftp_host or self.cfg.host, port=self.cfg.ftp_port,
                                 access_code=self.cfg.access_code(), timeout=timeout)

    async def start_plate(self, plate: PlateFile) -> None:
        # Field set as documented by the community (OpenBambuAPI, "project_file"); ids are "0" for LAN prints.
        await self._request({
            "print": {
                "command": "project_file", "param": plate.plate_gcode,
                "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
                "subtask_name": plate.plate_id, "url": self.cfg.print_url.format(file=plate.remote_name),
                "md5": plate.md5, "timelapse": False, "bed_type": "auto", "bed_levelling": True,
                "flow_cali": False, "vibration_cali": False, "layer_inspect": False,
                "use_ams": self.cfg.use_ams, "ams_mapping": "",
            }
        })

    async def pause(self) -> None:
        await self._request({"print": {"command": "pause", "param": ""}})

    async def resume(self) -> None:
        await self._request({"print": {"command": "resume", "param": ""}})

    async def cancel(self) -> None:
        await self._request({"print": {"command": "stop", "param": ""}})

    def _busy(self) -> bool:
        return self.raw.get("gcode_state") in BAMBU_BUSY

    async def _calibrate(self, routine: Routine, temperature: int | None) -> None:
        if routine.bambu_gcode:
            command = "gcode_line"
            await self._request({"print": {"command": command, "param": f"{routine.bambu_gcode}\n"}})
        else:
            command = "calibration"
            await self._request({"print": {"command": command, "option": routine.bambu_option}})
        # Started = the printer shows it is busy, or (a short G-code line) acknowledged it.
        if not await _poll(lambda: self._busy() or self.last_reply(command) == "success", CALIBRATION_START_S):
            reply = self.last_reply(command)
            raise CalibrationError(f"the printer did not start {routine.label.lower()} within {CALIBRATION_START_S:.0f} s"
                                   + (f" (printer replied {reply})" if reply else " (no reply: command ignored?)"))
        reply = self.last_reply(command)
        if reply and reply != "success":
            raise CalibrationError(f"the printer refused {routine.label.lower()}: {reply}")
        await _poll(lambda: not self._busy() or not self.online, routine.max_minutes * 60)
        if not self.online:
            raise CalibrationError("the printer went offline during the calibration")
        if self._busy():
            raise CalibrationError(f"{routine.label} still running after {routine.max_minutes} min")
        if self.raw.get("gcode_state") == "FAILED":
            raise CalibrationError(f"the printer reported error {self.raw.get('print_error')}")

    async def abort(self) -> None:
        await self.cancel()

    def to_state(self) -> PrinterState:
        r = self.raw
        err = r.get("print_error")
        status = BAMBU_STATUS.get(r.get("gcode_state", ""), PrinterStatus.IDLE)
        calibrating = self.calibrating is not None and status != PrinterStatus.FAILED
        return PrinterState(
            printer_id=self.cfg.id, vendor=self.vendor,
            status=PrinterStatus.CALIBRATING if calibrating else status,
            plate_id=None if calibrating else r.get("subtask_name") or None,
            progress=min(max(float(r.get("mc_percent", 0)) / 100, 0.0), 1.0),
            layer=r.get("layer_num"), total_layers=r.get("total_layer_num"),
            nozzle_temp=r.get("nozzle_temper"), nozzle_target=r.get("nozzle_target_temper"),
            bed_temp=r.get("bed_temper"), bed_target=r.get("bed_target_temper"),
            remaining_s=int(r["mc_remaining_time"]) * 60 if r.get("mc_remaining_time") is not None else None,
            error_code=str(err) if err else None,
            observed_at=datetime.now(UTC),
            hardware=bambu_hardware(r, self.firmware),
        )


# ---------------------------------------------------------------- Klipper / Moonraker

MOONRAKER_STATUS = {
    "standby": PrinterStatus.IDLE,
    "printing": PrinterStatus.PRINTING,
    "paused": PrinterStatus.PAUSED,
    "complete": PrinterStatus.FINISHED,
    "cancelled": PrinterStatus.FAILED,
    "error": PrinterStatus.FAILED,
}

MOONRAKER_OBJECTS = {
    "print_stats": None, "extruder": None, "heater_bed": None, "display_status": None,
    # hardware view; Klipper leaves out objects a printer does not have (no chamber sensor, no bed mesh, ...)
    "fan": None, "heater_fan hotend_fan": None, "temperature_sensor chamber": None, "gcode_move": None,
    "toolhead": None, "bed_mesh": None, "system_stats": None,
}


class MoonrakerAdapter(PrinterAdapter):
    vendor = "moonraker"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._ws = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    async def _session(self) -> None:
        async with websockets.connect(self.cfg.url, ping_interval=10, ping_timeout=10) as ws:
            self._ws = ws
            reader = asyncio.create_task(self._reader(ws))
            try:
                await self.call("server.connection.identify",
                                {"client_name": "printfarm", "version": "0.1", "type": "agent", "url": ""})
                with contextlib.suppress(Exception):
                    info = await self.call("printer.info")
                    self.firmware = (info or {}).get("software_version") or self.firmware
                res = await self.call("printer.objects.subscribe", {"objects": MOONRAKER_OBJECTS})
                _deep_merge(self.raw, res.get("status", {}))
                await self._connected()
                self.publish_state()
                await reader
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
                self._ws = None
                for f in self._pending.values():
                    f.cancel()
                self._pending.clear()

    async def _reader(self, ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self._pending:
                fut = self._pending.pop(msg["id"])
                if "error" in msg:
                    fut.set_exception(RuntimeError(msg["error"]))
                else:
                    fut.set_result(msg.get("result"))
            elif msg.get("method") == "notify_status_update":
                _deep_merge(self.raw, msg["params"][0])
                self.publish_state()

    async def call(self, method: str, params: dict | None = None, timeout: float = 10) -> Any:
        if self._ws is None:
            raise ConnectionError(f"{self.cfg.id} not connected")
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": rid}))
        return await asyncio.wait_for(fut, timeout)

    async def upload(self, plate: PlateFile, timeout: float) -> float:
        return await upload_moonraker(plate, base_url=self.cfg.http_base(), api_key=self.cfg.api_key(), timeout=timeout)

    async def start_plate(self, plate: PlateFile) -> None:
        await self.call("printer.print.start", {"filename": plate.remote_name})

    async def pause(self) -> None:
        await self.call("printer.print.pause")

    async def resume(self) -> None:
        await self.call("printer.print.resume")

    async def cancel(self) -> None:
        await self.call("printer.print.cancel")

    async def _calibrate(self, routine: Routine, temperature: int | None) -> None:
        # printer.gcode.script answers when Klipper has run the whole script, so the reply is the finish.
        script = routine.klipper_gcode.format(t=temperature)
        try:
            await self.call("printer.gcode.script", {"script": script}, timeout=routine.max_minutes * 60)
        except TimeoutError as exc:
            raise CalibrationError(f"{routine.label} still running after {routine.max_minutes} min") from exc
        except RuntimeError as exc:  # the JSON-RPC error: Klipper's message (e.g. "Unknown command")
            detail = exc.args[0].get("message") if exc.args and isinstance(exc.args[0], dict) else str(exc)
            raise CalibrationError(f"Klipper: {detail}") from exc

    async def abort(self) -> None:
        # A running G-code script cannot be cancelled; the emergency stop halts the heaters and motors.
        await self.call("printer.emergency_stop")

    def hardware(self) -> PrinterHardware:
        r = self.raw
        fans = {}
        for name, key in (("part", "fan"), ("hotend", "heater_fan hotend_fan")):
            speed = _num((r.get(key) or {}).get("speed"))
            if speed is not None:
                fans[name] = round(speed * 100)
        factor = _num((r.get("gcode_move") or {}).get("speed_factor"))
        return PrinterHardware(
            chamber_temp=_num((r.get("temperature_sensor chamber") or {}).get("temperature")), fans=fans,
            speed_pct=round(factor * 100) if factor is not None else None, nozzle_diameter=self.cfg.nozzle_mm,
            firmware=self.firmware, stage=self.calibrating.label.lower() if self.calibrating else None,
            homed_axes=(r.get("toolhead") or {}).get("homed_axes"),
            bed_mesh=(r.get("bed_mesh") or {}).get("profile_name") or None,
            host_load=_num((r.get("system_stats") or {}).get("sysload")),
        )

    def to_state(self) -> PrinterState:
        r = self.raw
        ps, ex, bed = r.get("print_stats", {}), r.get("extruder", {}), r.get("heater_bed", {})
        info = ps.get("info") or {}
        fname = ps.get("filename") or ""
        status = MOONRAKER_STATUS.get(ps.get("state", "standby"), PrinterStatus.IDLE)
        calibrating = self.calibrating is not None and status not in (PrinterStatus.PRINTING, PrinterStatus.PAUSED)
        return PrinterState(
            printer_id=self.cfg.id, vendor=self.vendor,
            status=PrinterStatus.CALIBRATING if calibrating else status,
            plate_id=None if calibrating else PurePosixPath(fname).stem or None,
            progress=min(max(float(r.get("display_status", {}).get("progress", 0.0)), 0.0), 1.0),
            layer=info.get("current_layer"), total_layers=info.get("total_layer"),
            nozzle_temp=ex.get("temperature"), nozzle_target=ex.get("target"),
            bed_temp=bed.get("temperature"), bed_target=bed.get("target"),
            error_code=ps.get("message") or None if ps.get("state") == "error" else None,
            observed_at=datetime.now(UTC),
            hardware=self.hardware(),
        )


ADAPTERS: dict[str, type[PrinterAdapter]] = {"bambu": BambuMqttAdapter, "moonraker": MoonrakerAdapter}


def make_adapter(cfg: PrinterCfg, on_state: StateCb, on_connection: ConnCb) -> PrinterAdapter:
    return ADAPTERS[cfg.vendor](cfg, on_state, on_connection)
