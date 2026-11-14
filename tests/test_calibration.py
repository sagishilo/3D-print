"""Calibration (Phase 4): the routine table, the agent's refusal rules, and the real adapters against the simulators
(Klipper over its WebSocket; Bambu through the simulator's command handler, without an MQTT broker)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from datetime import UTC, datetime

import pytest

from printfarm.agents.telemetry.adapters import CalibrationError, bambu_hardware, make_adapter
from printfarm.agents.telemetry.agent import calibration_refusal
from printfarm.config import PrinterCfg
from printfarm.contracts.calibration import BY_KEY, routines_for, target_temperature
from printfarm.contracts.messages import CalibratePrinter, PrinterState, PrinterStatus
from printfarm.simulator import printers as sim
from printfarm.simulator.moonraker import run_moonraker
from printfarm.simulator.printers import SimPrinter, bambu_report, handle_bambu_command


@pytest.fixture(autouse=True)
def fast_calibrations(monkeypatch):
    for routine in sim.CALIBRATION_S:
        monkeypatch.setitem(sim.CALIBRATION_S, routine, 1.0)


async def until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------- routine table


def test_routines_per_vendor_and_pid_temperatures():
    assert {r.key for r in routines_for("bambu")} == {"home", "bed_level", "vibration", "motor_noise"}
    assert {r.key for r in routines_for("moonraker")} == {"home", "bed_level", "vibration", "nozzle_pid", "bed_pid"}
    assert target_temperature(BY_KEY["home"], None) is None
    assert target_temperature(BY_KEY["nozzle_pid"], None) == 220
    assert target_temperature(BY_KEY["bed_pid"], 90) == 90
    with pytest.raises(ValueError, match="40-110"):
        target_temperature(BY_KEY["bed_pid"], 150)


# ---------------------------------------------------------------- refusal rules (pure)


def state(status: PrinterStatus) -> PrinterState:
    return PrinterState(printer_id="p1", vendor="bambu", status=status, observed_at=datetime.now(UTC))


def cmd(routine: str = "bed_level", **kw) -> CalibratePrinter:
    return CalibratePrinter(printer_id="p1", routine=routine, bed_clear_confirmed=kw.pop("bed", True), **kw)


@pytest.mark.parametrize(("command", "vendor", "online", "status", "busy", "reason"), [
    (cmd(), "bambu", True, PrinterStatus.IDLE, None, None),
    (cmd(), "bambu", True, PrinterStatus.FINISHED, None, None),
    (cmd(), None, True, PrinterStatus.IDLE, None, "unknown printer"),
    (cmd("nozzle_pid"), "bambu", True, PrinterStatus.IDLE, None, "nozzle_pid is not available on this printer"),
    (cmd(bed=False), "bambu", True, PrinterStatus.IDLE, None, "the bed was not confirmed empty"),
    (cmd(), "bambu", False, PrinterStatus.IDLE, None, "printer offline"),
    (cmd(), "bambu", True, None, None, "no state from the printer yet"),
    (cmd(), "bambu", True, PrinterStatus.PRINTING, None, "printer is printing"),
    (cmd(), "bambu", True, PrinterStatus.PAUSED, None, "printer is paused"),
    (cmd(), "bambu", True, PrinterStatus.CALIBRATING, None, "printer is calibrating"),
    (cmd(), "bambu", True, PrinterStatus.IDLE, "a plate is being started", "a plate is being started"),
    (cmd("bed_pid", temperature=150), "moonraker", True, PrinterStatus.IDLE, None, "Bed PID tuning: temperature must be 40-110 °C"),
])
def test_calibration_runs_only_on_an_idle_printer_with_an_empty_bed(command, vendor, online, status, busy, reason):
    s = state(status) if status else None
    assert calibration_refusal(command, vendor=vendor, online=online, state=s, busy=busy) == reason


# ---------------------------------------------------------------- Klipper: the real adapter against the simulator


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def klipper(faults: frozenset[str] = frozenset()):
    printer = SimPrinter("voron-sim", print_seconds=30, faults=faults, model="klipper")
    port = free_port()
    server = asyncio.create_task(run_moonraker(printer, port, "127.0.0.1"))
    cfg = PrinterCfg(id="voron-sim", vendor="moonraker", bed_mm=(300, 300), nozzle_mm=0.4, material="PLA",
                     color="black", url=f"ws://127.0.0.1:{port}/websocket", simulated=True)
    states: list[PrinterState] = []

    async def on_state(s):
        states.append(s)

    async def on_connection(_up):
        pass

    adapter = make_adapter(cfg, on_state, on_connection)
    run = asyncio.create_task(adapter.run())
    try:
        await until(lambda: adapter.online and states)
        yield printer, adapter, states
    finally:
        for t in (run, server):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


async def test_klipper_bed_mesh_runs_to_the_end_and_reports_hardware():
    async with klipper() as (printer, adapter, states):
        hw = states[-1].hardware
        assert hw.firmware == "v0.12.0-415-gsim" and hw.homed_axes == "" and hw.host_load is not None
        task = asyncio.create_task(adapter.calibrate(BY_KEY["bed_level"], None))
        await until(lambda: states[-1].status == PrinterStatus.CALIBRATING)
        assert states[-1].hardware.stage == "bed leveling" and states[-1].plate_id is None
        await asyncio.wait_for(task, 10)
        await until(lambda: states[-1].status == PrinterStatus.IDLE)
        hw = states[-1].hardware
        assert hw.bed_mesh == "default" and hw.homed_axes == "xyz" and hw.nozzle_diameter == 0.4


async def test_klipper_pid_heats_to_the_requested_target():
    async with klipper() as (printer, adapter, states):
        task = asyncio.create_task(adapter.calibrate(BY_KEY["nozzle_pid"], 235))
        await until(lambda: printer.nozzle_target == 235.0)
        await asyncio.wait_for(task, 10)
        assert printer.nozzle_target == 0.0


async def test_klipper_error_is_reported_and_emergency_stop_halts_a_routine():
    async with klipper(frozenset({"calibration_fail"})) as (printer, adapter, states):
        with pytest.raises(CalibrationError, match="Klipper: bed_level: probe did not trigger"):
            await asyncio.wait_for(adapter.calibrate(BY_KEY["bed_level"], None), 10)
    async with klipper() as (printer, adapter, states):
        task = asyncio.create_task(adapter.calibrate(BY_KEY["vibration"], None))
        await until(lambda: printer.calibration == "vibration")
        await adapter.abort()
        with pytest.raises(CalibrationError, match="Emergency stop"):
            await asyncio.wait_for(task, 10)
        await until(lambda: states[-1].status == PrinterStatus.FAILED)


# ---------------------------------------------------------------- Bambu: the real adapter, simulator in-process


class SimLink:
    """Stands in for the MQTT broker: requests go to the simulator's command handler, reports back to the adapter."""

    def __init__(self, adapter, printer: SimPrinter):
        self.adapter, self.printer = adapter, printer

    async def publish(self, _topic, payload, qos=0):
        body = json.loads(payload).get("print") or {}
        answer = handle_bambu_command(self.printer, body)
        if answer is not None:
            ok, reason = answer
            self.adapter._replies[body["command"]] = "success" if ok else f"failed: {reason}"
        self.report()

    def report(self):
        self.adapter.raw.update(bambu_report(self.printer)["print"])


@contextlib.asynccontextmanager
async def bambu(faults: frozenset[str] = frozenset()):
    printer = SimPrinter("p1-sim", print_seconds=30, faults=faults, model="p1", color="white")
    cfg = PrinterCfg(id="p1-sim", vendor="bambu", bed_mm=(256, 256), nozzle_mm=0.4, material="PLA", color="white",
                     host="127.0.0.1", serial="SIM1", simulated=True)

    async def ignore(*_):
        pass

    adapter = make_adapter(cfg, ignore, ignore)
    link = SimLink(adapter, printer)
    adapter._client, adapter.online = link, True
    link.report()

    async def clock():  # the simulator's physics at 10x speed
        while True:
            printer.tick(0.5)
            link.report()
            await asyncio.sleep(0.05)

    ticker = asyncio.create_task(clock())
    try:
        yield printer, adapter
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker


async def test_bambu_bed_leveling_is_followed_from_the_printer_state():
    async with bambu() as (printer, adapter):
        task = asyncio.create_task(adapter.calibrate(BY_KEY["bed_level"], None))
        await until(lambda: adapter.to_state().status == PrinterStatus.CALIBRATING)
        assert adapter.to_state().hardware.stage == "auto bed leveling"
        await asyncio.wait_for(task, 10)
        assert adapter.to_state().status == PrinterStatus.IDLE and printer.calibration == ""


async def test_bambu_ignored_or_failed_calibration_raises(monkeypatch):
    monkeypatch.setattr("printfarm.agents.telemetry.adapters.CALIBRATION_START_S", 1.0)
    async with bambu(frozenset({"ignore_commands"})) as (_printer, adapter):
        with pytest.raises(CalibrationError, match="did not start bed leveling .*no reply"):
            await adapter.calibrate(BY_KEY["bed_level"], None)
    async with bambu(frozenset({"calibration_fail"})) as (_printer, adapter):
        with pytest.raises(CalibrationError, match="reported error"):
            await asyncio.wait_for(adapter.calibrate(BY_KEY["motor_noise"], None), 10)


def test_bambu_hardware_readings():
    printer = SimPrinter("x1-sim", print_seconds=30, faults=frozenset({"hms_warning"}), model="x1", color="black")
    printer.status, printer.progress, printer.nozzle = "printing", 0.5, 220.0
    hw = bambu_hardware(bambu_report(printer)["print"], "01.08.05.00")
    assert hw.fans == {"part": 100, "aux": 40, "chamber": 27, "hotend": 100}   # 0-15 levels: 30 % is level 4 = 27 %
    assert hw.speed_mode == "standard" and hw.speed_pct == 100 and hw.nozzle_type == "hardened_steel"
    assert hw.wifi_dbm == -52 and hw.firmware == "01.08.05.00" and hw.nozzle_diameter == 0.4
    assert hw.hms == ["0300_0100_0001_0007"]
    tray = hw.ams[0].trays[0]
    assert (tray.material, tray.color, tray.remaining_pct) == ("PLA", "#000000", 60)
    assert len(hw.ams[0].trays) == 4 and hw.ams[0].humidity_level == 4
