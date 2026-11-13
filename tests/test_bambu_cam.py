"""bambu-cam (the P1/A1 frame-reader) against the simulated port-6000 camera, including failure modes."""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import time
from pathlib import Path

import pytest

from printfarm.simulator.assets import server_tls_context
from printfarm.simulator.camera import DROP_AFTER, STALL_AFTER, BambuCameraServer
from printfarm.simulator.printers import SimPrinter

CODE = "12345678"
_spec = importlib.util.spec_from_file_location("bambu_cam", Path(__file__).resolve().parents[1] / "bambu-cam" / "bambu_cam.py")
bambu_cam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bambu_cam)


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(bambu_cam, "READ_TIMEOUT_S", 0.6)
    monkeypatch.setattr(bambu_cam, "RETRY_S", 0.2)
    monkeypatch.setattr(bambu_cam, "IDLE_CLOSE_S", 10)


async def camera_server(*faults: str) -> BambuCameraServer:
    server = BambuCameraServer(SimPrinter("p1s-sim", 5, frozenset(faults), access_code=CODE), server_tls_context(), fps=25)
    await server.start("127.0.0.1", 0)
    return server


async def until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.02)


async def watch(server: BambuCameraServer, code: str = CODE):
    cam = bambu_cam.Camera("p1s-sim", "127.0.0.1", code, server.port)
    cam.viewers = 1
    cam.ensure_running()
    return cam


async def stop(cam, server) -> None:
    cam.viewers = 0
    if cam.task:
        cam.task.cancel()
    server.server.close()


async def test_frames_arrive():
    server = await camera_server()
    cam = await watch(server)
    try:
        await until(lambda: cam.frames >= 3)
        assert cam.connected and cam.frame[:2] == b"\xff\xd8" and cam.last_error == ""
    finally:
        await stop(cam, server)


@pytest.mark.parametrize("code,faults", [("00000000", ()), (CODE, ("camera_auth_fail",))])
async def test_refused_login_is_reported_and_no_frame_is_served(code, faults):
    server = await camera_server(*faults)
    cam = await watch(server, code)
    try:
        await until(lambda: "not a JPEG" in cam.last_error)
        assert cam.frames == 0 and cam.frame is None and not cam.connected
    finally:
        await stop(cam, server)


async def test_dropped_connection_reconnects_and_frames_continue():
    server = await camera_server("camera_drop")
    cam = await watch(server)
    try:
        await until(lambda: cam.frames >= DROP_AFTER + 3, timeout=8)  # needed at least one reconnect
        assert cam.frames > DROP_AFTER
    finally:
        await stop(cam, server)


async def test_stalled_camera_times_out_and_the_stale_frame_is_dropped(monkeypatch):
    monkeypatch.setattr(bambu_cam, "RETRY_S", 3.0)  # stay disconnected long enough to look
    server = await camera_server("camera_stall")
    cam = await watch(server)
    try:
        await until(lambda: cam.frames >= STALL_AFTER)
        await until(lambda: "timeout" in cam.last_error)
        assert cam.frame is None and not cam.connected  # a snapshot now waits instead of serving an old frame
    finally:
        await stop(cam, server)


def test_camera_addresses():
    assert bambu_cam.parse_cams("p1s-01=192.168.50.23, sim=simulator:6001") == [
        ("p1s-01", "192.168.50.23", 6000), ("sim", "simulator", 6001)]


async def http_get(port: int, path: str) -> tuple[str, bytes]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await w.drain()
    data = await asyncio.wait_for(r.read(), 10)
    w.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return head.decode(), body


async def test_snapshot_fails_fast_with_the_reason_when_the_login_is_refused():
    server = await camera_server("camera_auth_fail")
    cams = {"p1s-sim": bambu_cam.Camera("p1s-sim", "127.0.0.1", CODE, server.port)}
    http = await asyncio.start_server(functools.partial(bambu_cam.handle, cams=cams), "127.0.0.1", 0)
    port = http.sockets[0].getsockname()[1]
    try:
        for path in ("/p1s-sim.jpg", "/p1s-sim.mjpeg"):  # go2rtc reads the MJPEG stream
            t0 = time.monotonic()
            head, body = await http_get(port, path)
            assert "503" in head and b"not a JPEG" in body and time.monotonic() - t0 < 5  # not the 15 s frame timeout
    finally:
        cams["p1s-sim"].viewers = 0
        if cams["p1s-sim"].task:
            cams["p1s-sim"].task.cancel()
        http.close()
        server.server.close()


async def test_http_snapshot_and_status():
    server = await camera_server()
    cams = {"p1s-sim": bambu_cam.Camera("p1s-sim", "127.0.0.1", CODE, server.port)}
    http = await asyncio.start_server(functools.partial(bambu_cam.handle, cams=cams), "127.0.0.1", 0)
    port = http.sockets[0].getsockname()[1]
    try:
        head, body = await http_get(port, "/p1s-sim.jpg")
        assert "200 OK" in head and "image/jpeg" in head and body[:2] == b"\xff\xd8"
        head, body = await http_get(port, "/status")
        status = json.loads(body)["p1s-sim"]
        assert status["connected"] and status["frames"] >= 1 and status["host"].endswith(f":{server.port}")
        head, _ = await http_get(port, "/unknown.jpg")
        assert "404" in head
    finally:
        cams["p1s-sim"].viewers = 0
        if cams["p1s-sim"].task:
            cams["p1s-sim"].task.cancel()
        http.close()
        server.server.close()
