"""Simulated Klipper/Moonraker printer: HTTP API and WebSocket JSON-RPC on ONE port, like the real Moonraker.

HTTP:  GET  /server/info                  -> {"result": {"klippy_state": "ready", ...}}
       POST /server/files/upload          -> multipart "file" (+ "root"), stored in the printer's memory
       GET  /server/files/list            -> uploaded files
       GET  /webcam/?action=snapshot|stream  Crowsnest-style JPEG / MJPEG
WS:    /websocket                         -> JSON-RPC 2.0 (printer.objects.subscribe, printer.print.start, ...)

WebSocket upgrades are passed byte-for-byte to an internal ``websockets`` server; everything else is plain HTTP
handled here, so no extra web framework is needed.

Faults: ``upload_fail`` (HTTP 507), ``upload_flaky`` (first upload of a file gets HTTP 503), ``ignore_commands``
(every print command answers "ok" but does nothing), ``ignore_pause`` (pause answers "ok" but does nothing),
plus the camera faults in ``camera.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import websockets

from printfarm.simulator.assets import camera_frames
from printfarm.simulator.camera import write_mjpeg

if TYPE_CHECKING:
    from printfarm.simulator.printers import SimPrinter

log = logging.getLogger("simulator.moonraker")
MAX_BODY = 512 * 1024 * 1024
TICK_S = 0.5

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


# ---------------------------------------------------------------- JSON-RPC over WebSocket


def _rpc(p: SimPrinter, method: str, params: dict) -> tuple[object | None, dict | None]:
    """(result, error) for one JSON-RPC call."""
    ignored = p.has("ignore_commands")
    match method:
        case "server.connection.identify":
            return {"connection_id": id(p)}, None
        case "printer.objects.subscribe" | "printer.objects.query":
            return {"eventtime": time.time(), "status": moonraker_status(p)}, None
        case "server.info":
            return {"klippy_state": "ready", "klippy_connected": True}, None
        case "printer.print.start":
            if ignored:
                return "ok", None
            name = str(params.get("filename", ""))
            err = p.start_file(name, md5=None, plate=PurePosixPath(name).name.removesuffix(".gcode"))
            return (None, {"code": 400, "message": err}) if err else ("ok", None)
        case "printer.print.pause":
            if not (ignored or p.has("ignore_pause")):
                p.pause()
            return "ok", None
        case "printer.print.resume":
            if not ignored:
                p.resume()
            return "ok", None
        case "printer.print.cancel":
            if not ignored:
                p.cancel()
            return "ok", None
    return None, {"code": -32601, "message": "Method not found"}


async def _ws_handler(p: SimPrinter, ws) -> None:
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
            result, error = _rpc(p, method, params)
            if method == "printer.objects.subscribe":
                pusher = pusher or asyncio.create_task(push())
            reply = {"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})}
            await ws.send(json.dumps(reply))
    except websockets.ConnectionClosed:
        pass
    finally:
        if pusher:
            pusher.cancel()


# ---------------------------------------------------------------- HTTP front (+ WebSocket pass-through)


async def _respond(w: asyncio.StreamWriter, status: str, body: bytes, ctype: str = "application/json") -> None:
    w.write(f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            .encode() + body)
    await w.drain()


async def _json(w: asyncio.StreamWriter, status: str, obj: object) -> None:
    await _respond(w, status, json.dumps(obj).encode())


def _multipart_files(content_type: str, body: bytes) -> dict[str, tuple[str, bytes]]:
    """{field: (filename, data)} from a multipart/form-data body (stdlib email parser)."""
    msg = BytesParser(policy=HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        out[name] = (part.get_filename() or "", part.get_payload(decode=True) or b"")
    return out


async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, asyncio.CancelledError):
        while chunk := await r.read(1 << 16):
            w.write(chunk)
            await w.drain()
    with contextlib.suppress(Exception):
        w.close()


async def _front(p: SimPrinter, ws_port: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    method, target, *_ = (lines[0].split(" ") + ["", ""])[:3]
    headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
    url = urlsplit(target)
    try:
        if headers.get("upgrade", "").lower() == "websocket":
            ur, uw = await asyncio.open_connection("127.0.0.1", ws_port)
            uw.write(head)
            await uw.drain()
            await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))
            return
        await _http(p, method, url.path, parse_qs(url.query), headers, reader, writer)
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def _http(p, method, path, query, headers, reader, writer) -> None:
    if method == "GET" and path == "/server/info":
        await _json(writer, "200 OK", {"result": {"klippy_connected": True, "klippy_state": "ready",
                                                  "moonraker_version": "printfarm-simulator"}})
    elif method == "GET" and path == "/server/files/list":
        await _json(writer, "200 OK", {"result": [{"path": n, "size": len(d)} for n, d in sorted(p.files.items())]})
    elif method == "POST" and path == "/server/files/upload":
        size = int(headers.get("content-length", "-1"))
        if size < 0 or size > MAX_BODY:
            await _json(writer, "411 Length Required", {"error": {"code": 411, "message": "Content-Length required"}})
            return
        body = await reader.readexactly(size)
        files = _multipart_files(headers.get("content-type", ""), body)
        if "file" not in files or not files["file"][0]:
            await _json(writer, "400 Bad Request", {"error": {"code": 400, "message": "no file field"}})
        elif p.has("upload_fail"):
            await _json(writer, "507 Insufficient Storage", {"error": {"code": 507, "message": "No space left"}})
        elif p.has("upload_flaky") and files["file"][0] not in p.flaky_failed:
            p.flaky_failed.add(files["file"][0])
            await _json(writer, "503 Service Unavailable", {"error": {"code": 503, "message": "busy, try again"}})
        else:
            name, data = files["file"]
            p.files[name] = data
            log.info("%s: received %s (%d bytes) over HTTP", p.id, name, len(data))
            await _json(writer, "201 Created", {"result": {"item": {"path": name, "root": "gcodes", "size": len(data)},
                                                           "print_started": False, "action": "create_file"}})
    elif method == "GET" and path.rstrip("/") == "/webcam":
        action = query.get("action", ["stream"])[0]
        if p.has("camera_off"):
            await _respond(writer, "503 Service Unavailable", b"camera offline", "text/plain")
        elif action == "snapshot":
            await _respond(writer, "200 OK", camera_frames()[int(time.time()) % 4], "image/jpeg")
        else:
            await write_mjpeg(writer, p)
    else:
        await _json(writer, "404 Not Found", {"error": {"code": 404, "message": f"{method} {path}"}})


async def run_moonraker(p: SimPrinter, port: int, bind: str = "0.0.0.0") -> None:
    async def physics():
        while True:
            p.tick(TICK_S)
            await asyncio.sleep(TICK_S)

    async with websockets.serve(lambda ws: _ws_handler(p, ws), "127.0.0.1", 0) as ws_server:
        ws_port = ws_server.sockets[0].getsockname()[1]
        front = await asyncio.start_server(lambda r, w: _front(p, ws_port, r, w), bind, port)
        async with front:
            log.info("moonraker sim %s listening on %s:%d (HTTP + WebSocket)", p.id, bind, port)
            await physics()
