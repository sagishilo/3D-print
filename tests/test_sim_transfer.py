"""Plate upload against the simulated printers: implicit FTPS (Bambu) and HTTP + WebSocket (Moonraker)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import socket

import httpx
import pytest
import websockets

from printfarm.agents.telemetry.transfer import PlateFile, UploadError, upload_ftps, upload_moonraker
from printfarm.simulator.assets import server_tls_context
from printfarm.simulator.camera import DROP_AFTER
from printfarm.simulator.ftps import SimFtpsServer
from printfarm.simulator.moonraker import run_moonraker
from printfarm.simulator.printers import SimPrinter

CODE = "12345678"


def plate_file(tmp_path, name: str, data: bytes) -> PlateFile:
    path = tmp_path / name
    path.write_bytes(data)
    return PlateFile(name.split(".")[0], path, name, hashlib.md5(data).hexdigest(), len(data))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- Bambu: implicit FTPS


@pytest.fixture
async def ftps():
    printer = SimPrinter("bambu-sim", 5, access_code=CODE)
    server = SimFtpsServer(printer, server_tls_context())
    await server.start("127.0.0.1", 0)
    yield printer, server
    server.server.close()


async def test_ftps_upload_reuses_the_tls_session(ftps, tmp_path):
    printer, server = ftps
    data = bytes(range(256)) * 2000  # 512 KB
    pf = plate_file(tmp_path, "plate-1.gcode.3mf", data)
    seconds = await upload_ftps(pf, host="127.0.0.1", port=server.port, access_code=CODE, timeout=30)
    assert printer.files["plate-1.gcode.3mf"] == data and seconds < 30


async def test_ftps_without_session_reuse_is_refused_like_a_real_printer(ftps, tmp_path):
    _, server = ftps
    pf = plate_file(tmp_path, "plate-2.gcode.3mf", b"x" * 1000)
    with pytest.raises(UploadError, match="522") as err:
        await upload_ftps(pf, host="127.0.0.1", port=server.port, access_code=CODE, timeout=30, reuse_session=False)
    assert not err.value.transient


async def test_ftps_wrong_access_code(ftps, tmp_path):
    _, server = ftps
    pf = plate_file(tmp_path, "plate-3.gcode.3mf", b"x")
    with pytest.raises(UploadError, match="530") as err:
        await upload_ftps(pf, host="127.0.0.1", port=server.port, access_code="00000000", timeout=30)
    assert not err.value.transient


async def test_ftps_no_space_is_permanent(ftps, tmp_path):
    printer, server = ftps
    printer.faults = frozenset({"upload_fail"})
    pf = plate_file(tmp_path, "plate-4.gcode.3mf", b"x" * 10)
    with pytest.raises(UploadError, match="552") as err:
        await upload_ftps(pf, host="127.0.0.1", port=server.port, access_code=CODE, timeout=30)
    assert not err.value.transient and not printer.files


async def test_ftps_temporary_failure_is_transient_and_the_retry_works(ftps, tmp_path):
    printer, server = ftps
    printer.faults = frozenset({"upload_flaky"})
    pf = plate_file(tmp_path, "plate-5.gcode.3mf", b"y" * 100)
    with pytest.raises(UploadError, match="451") as err:
        await upload_ftps(pf, host="127.0.0.1", port=server.port, access_code=CODE, timeout=30)
    assert err.value.transient
    await upload_ftps(pf, host="127.0.0.1", port=server.port, access_code=CODE, timeout=30)
    assert printer.files["plate-5.gcode.3mf"] == b"y" * 100


async def test_ftps_unreachable_printer_is_transient(tmp_path):
    pf = plate_file(tmp_path, "plate-6.gcode.3mf", b"z")
    with pytest.raises(UploadError) as err:
        await upload_ftps(pf, host="127.0.0.1", port=free_port(), access_code=CODE, timeout=10)
    assert err.value.transient


# ---------------------------------------------------------------- Moonraker: HTTP + WebSocket on one port


@pytest.fixture
async def moonraker():
    printer = SimPrinter("voron-sim", 5)
    port = free_port()
    task = asyncio.create_task(run_moonraker(printer, port, "127.0.0.1"))
    for _ in range(100):  # wait until it listens
        try:
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            break
        except OSError:
            await asyncio.sleep(0.05)
    yield printer, port
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def rpc(port: int, method: str, params: dict | None = None) -> dict:
    async with websockets.connect(f"ws://127.0.0.1:{port}/websocket") as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}))
        return json.loads(await ws.recv())


async def test_moonraker_upload_then_start_over_the_same_port(moonraker, tmp_path):
    printer, port = moonraker
    pf = plate_file(tmp_path, "plate-9.gcode", b"G28\nG1 X10\n")
    await upload_moonraker(pf, base_url=f"http://127.0.0.1:{port}", timeout=10)
    assert printer.files["plate-9.gcode"] == b"G28\nG1 X10\n"
    reply = await rpc(port, "printer.print.start", {"filename": "plate-9.gcode"})
    assert reply["result"] == "ok" and printer.status == "printing" and printer.plate == "plate-9"


async def test_moonraker_refuses_to_start_a_file_it_does_not_have(moonraker):
    printer, port = moonraker
    reply = await rpc(port, "printer.print.start", {"filename": "never-uploaded.gcode"})
    assert reply["error"]["code"] == 400 and printer.status == "failed"


async def test_moonraker_upload_errors(moonraker, tmp_path):
    printer, port = moonraker
    pf = plate_file(tmp_path, "plate-10.gcode", b"G28\n")
    printer.faults = frozenset({"upload_fail"})
    with pytest.raises(UploadError, match="507") as err:
        await upload_moonraker(pf, base_url=f"http://127.0.0.1:{port}", timeout=10)
    assert not err.value.transient
    printer.faults = frozenset({"upload_flaky"})
    with pytest.raises(UploadError, match="503") as err:
        await upload_moonraker(pf, base_url=f"http://127.0.0.1:{port}", timeout=10)
    assert err.value.transient
    await upload_moonraker(pf, base_url=f"http://127.0.0.1:{port}", timeout=10)
    assert "plate-10.gcode" in printer.files


async def test_ignored_and_obeyed_pause(moonraker):
    printer, port = moonraker
    printer.start("plate-x")
    printer.faults = frozenset({"ignore_pause"})
    assert (await rpc(port, "printer.print.pause"))["result"] == "ok" and printer.status == "printing"
    printer.faults = frozenset()
    assert (await rpc(port, "printer.print.pause"))["result"] == "ok" and printer.status == "paused"


async def test_server_info_for_preflight(moonraker):
    _, port = moonraker
    async with httpx.AsyncClient() as c:
        r = await c.get(f"http://127.0.0.1:{port}/server/info")
    assert r.json()["result"]["klippy_state"] == "ready"


# ---------------------------------------------------------------- Crowsnest webcam (Moonraker host)


async def read_mjpeg_frames(port: int, limit: int, timeout: float = 5) -> int:
    """Count JPEG parts in the MJPEG stream until ``limit`` or the server closes it."""
    frames = 0
    async with httpx.AsyncClient(timeout=timeout) as c, c.stream("GET", f"http://127.0.0.1:{port}/webcam/?action=stream") as r:
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        buf = b""
        async for chunk in r.aiter_bytes():
            buf += chunk
            frames = buf.count(b"Content-Type: image/jpeg")
            if frames >= limit:
                break
    return frames


async def test_webcam_snapshot_and_stream(moonraker):
    _, port = moonraker
    async with httpx.AsyncClient() as c:
        snap = await c.get(f"http://127.0.0.1:{port}/webcam/?action=snapshot")
    assert snap.headers["content-type"] == "image/jpeg" and snap.content[:2] == b"\xff\xd8"
    assert await read_mjpeg_frames(port, limit=3) >= 3


async def test_webcam_faults(moonraker):
    printer, port = moonraker
    printer.faults = frozenset({"camera_off"})
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"http://127.0.0.1:{port}/webcam/?action=snapshot")).status_code == 503
    printer.faults = frozenset({"camera_drop"})
    assert await read_mjpeg_frames(port, limit=DROP_AFTER + 10) == DROP_AFTER  # the stream ends early
