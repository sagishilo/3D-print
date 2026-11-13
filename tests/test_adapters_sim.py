"""The real Moonraker adapter (as used by the telemetry agent) against the simulated Klipper printer.

The Bambu adapter needs an MQTT broker; tests/test_e2e.py covers it end to end.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import socket
import time

import pytest

from printfarm.agents.telemetry.adapters import make_adapter
from printfarm.agents.telemetry.safety import pause_and_verify
from printfarm.agents.telemetry.transfer import PlateFile
from printfarm.config import PrinterCfg
from printfarm.contracts.messages import PrinterStatus
from printfarm.simulator.moonraker import run_moonraker
from printfarm.simulator.printers import SimPrinter


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.05)


@pytest.fixture
async def klipper(tmp_path):
    printer = SimPrinter("voron-sim", print_seconds=30)
    port = free_port()
    sim = asyncio.create_task(run_moonraker(printer, port, "127.0.0.1"))
    cfg = PrinterCfg(id="voron-sim", vendor="moonraker", bed_mm=(300, 300), nozzle_mm=0.4, material="PLA", color="black",
                     url=f"ws://127.0.0.1:{port}/websocket", simulated=True)
    states = []

    async def on_state(s):
        states.append(s)

    async def on_connection(_up):
        pass

    adapter = make_adapter(cfg, on_state, on_connection)
    run = asyncio.create_task(adapter.run())
    yield printer, cfg, adapter, states
    for t in (run, sim):
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


async def test_upload_start_verified_pause_resume_cancel(klipper, tmp_path):
    printer, cfg, adapter, states = klipper
    assert cfg.http_base() == cfg.url.replace("ws://", "http://").removesuffix("/websocket")
    await until(lambda: adapter.online)

    data = b"; plate-77\nG28\n"
    path = tmp_path / "plate-77.gcode"
    path.write_bytes(data)
    plate = PlateFile("plate-77", path, "plate-77.gcode", hashlib.md5(data).hexdigest(), len(data))
    await adapter.upload(plate, timeout=10)
    assert printer.files["plate-77.gcode"] == data

    await adapter.start_plate(plate)
    await until(lambda: states and states[-1].status == PrinterStatus.PRINTING and states[-1].plate_id == "plate-77")

    out = await pause_and_verify(send_pause=adapter.pause, current_state=lambda: states[-1],
                                 is_online=lambda: adapter.online, confirm_s=5, retries=0)
    assert out.confirmed and out.status == PrinterStatus.PAUSED

    await adapter.resume()
    await until(lambda: states[-1].status == PrinterStatus.PRINTING)
    await adapter.cancel()
    await until(lambda: states[-1].status == PrinterStatus.FAILED)


async def test_pause_ignored_by_the_printer_is_unconfirmed(klipper, tmp_path):
    printer, _cfg, adapter, states = klipper
    await until(lambda: adapter.online)
    printer.files["plate-8.gcode"] = b"G28\n"
    printer.faults = frozenset({"ignore_pause"})
    await adapter.start_plate(PlateFile("plate-8", tmp_path / "x", "plate-8.gcode", "", 4))
    await until(lambda: states and states[-1].status == PrinterStatus.PRINTING)
    out = await pause_and_verify(send_pause=adapter.pause, current_state=lambda: states[-1],
                                 is_online=lambda: adapter.online, confirm_s=1.5, retries=1)
    assert not out.confirmed and out.attempts == 2 and out.status == PrinterStatus.PRINTING and out.online
