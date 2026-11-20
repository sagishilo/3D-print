"""Printer onboarding: a one-off connection test for a printer that is not in the fleet yet ("חיבור מדפסת חדשה").

The dashboard sends what the operator read off the printer (IP, serial, access code). The gateway tries exactly what
the farm will do later: log in to the printer's MQTT (Bambu) or Moonraker API (Klipper), wait for one telemetry
report, and reach the file-upload and camera ports. Nothing is saved: the access code is used for this one test,
never stored, logged or audited. Adding the printer stays a file change (config/fleet.toml + cameras.env), which the
dashboard writes out for the operator to paste.

Only hosts on the local network are probed (private addresses, no loopback / link-local), on the ports a printer
uses, so this cannot be turned into a scanner of the internet or of the gateway's own host.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
import struct
import time
import uuid
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from . import auth

router = APIRouter(prefix="/api/v1/printers")

TIMEOUT_S = 5.0
REPORT_WAIT_S = 8.0
HOST = r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"   # an IPv4 address or a LAN host name


class ConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: Literal["bambu", "moonraker", "generic"]
    host: str = Field(pattern=HOST, max_length=253)
    # Bambu Lab
    model: Literal["x1", "p1", "a1", "a1-mini"] | None = None
    serial: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{8,20}$")
    access_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{8}$")
    mqtt_port: int = Field(default=8883, ge=1, le=65535)
    tls: bool = True
    ftp_host: str | None = Field(default=None, pattern=HOST, max_length=253)
    ftp_port: int = Field(default=990, ge=1, le=65535)
    camera_host: str | None = Field(default=None, pattern=HOST, max_length=253)
    camera_port: int | None = Field(default=None, ge=1, le=65535)
    # Klipper / Moonraker, and generic
    port: int | None = Field(default=None, ge=1, le=65535)
    api_key: str | None = Field(default=None, max_length=128)


class Refused(Exception):
    """A failure with a message meant for the operator as is (wrong code, no report, not a picture)."""


def _check(key: str, label: str, ok: bool, detail: str, *, optional: bool = False, ms: float = 0) -> dict:
    return {"key": key, "label": label, "ok": ok, "detail": detail, "optional": optional, "ms": round(ms)}


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # printers present self-signed certificates
    return ctx


def _err(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "no answer (timed out): is the printer on, and is the address right?"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused: the printer answered, but this port is closed"
    if isinstance(exc, socket.gaierror):
        return "unknown host name"
    if isinstance(exc, OSError) and exc.errno in (101, 113, 10051, 10065):
        return "network unreachable: is the printer on the same network as this PC?"
    return f"{type(exc).__name__}: {exc}"[:160]


async def lan_only(host: str) -> str:
    """Resolve ``host`` and refuse anything that is not on a private network. Returns the address to probe."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(422, f"{host}: unknown host name") from exc
    addrs = {ipaddress.ip_address(i[4][0]) for i in infos}
    for a in addrs:
        if not a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified:
            raise HTTPException(422, f"{host} ({a}) is not on the local network: printers are tested on the LAN only")
    return host


# ------------------------------------------------------------------------------------------------ probes
async def tcp(host: str, port: int, *, use_tls: bool = False) -> str:
    _reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=_tls_context() if use_tls else None), timeout=TIMEOUT_S)
    version = writer.get_extra_info("ssl_object").version() if use_tls else None
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, ssl.SSLError):
        pass
    return f"TLS ok ({version})" if use_tls else "port open"


async def bambu_report(host: str, port: int, use_tls: bool, serial: str, code: str) -> tuple[str, dict]:
    """Log in to the printer's MQTT broker and wait for one status report. Returns (login detail, report)."""
    import aiomqtt  # the gateway image installs it; imported here so the rest of the gateway does not need it

    login = ""
    try:
        async with aiomqtt.Client(host, port, username="bblp", password=code, identifier=f"printfarm-test-{uuid.uuid4().hex[:8]}",
                                  tls_context=_tls_context() if use_tls else None, timeout=TIMEOUT_S, keepalive=15) as client:
            login = "logged in as bblp"
            await client.subscribe(f"device/{serial}/report")
            await client.publish(f"device/{serial}/request",
                                 json.dumps({"pushing": {"command": "pushall", "version": 1, "push_target": 1}}))
            async with asyncio.timeout(REPORT_WAIT_S):
                async for message in client.messages:
                    try:
                        body = json.loads(message.payload).get("print")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(body, dict) and ("gcode_state" in body or "nozzle_temper" in body):
                        return login, body
    except TimeoutError as exc:
        if login:
            raise Refused("logged in, but no status report came") from exc
        raise
    except aiomqtt.MqttError as exc:
        text = str(exc).lower()
        if "authori" in text or "password" in text or getattr(exc, "rc", None) in (4, 5, 134, 135):
            raise Refused("the printer refused the access code") from exc
        if "timed out" in text:
            raise TimeoutError from exc
        raise ConnectionError(str(exc)) from exc
    raise Refused("logged in, but no status report came")


async def p1_camera(host: str, port: int, code: str) -> str:
    """Bambu P1 / A1 camera: TLS on port 6000, an 80-byte login, then one JPEG frame (same as bambu-cam)."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=_tls_context()), timeout=TIMEOUT_S)
    try:
        writer.write(struct.pack("<IIII", 0x40, 0x3000, 0, 0) + b"bblp".ljust(32, b"\0") + code.encode().ljust(32, b"\0"))
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(16), timeout=TIMEOUT_S)
        size = struct.unpack("<I", header[:4])[0]
        if not 0 < size <= 4_000_000:
            raise Refused("the camera did not send a picture: wrong access code?")
        jpeg = await asyncio.wait_for(reader.readexactly(size), timeout=TIMEOUT_S)
        if jpeg[:2] != b"\xff\xd8":
            raise Refused("the camera did not send a picture: wrong access code?")
        return f"picture received ({size // 1024} KB)"
    except asyncio.IncompleteReadError as exc:
        raise Refused("the camera closed the connection: wrong access code?") from exc
    finally:
        writer.close()


async def _timed(coro) -> tuple[bool, str, float, object]:
    t0 = time.perf_counter()
    try:
        value = await coro
        return True, value if isinstance(value, str) else "", (time.perf_counter() - t0) * 1000, value
    except Refused as exc:
        return False, str(exc), (time.perf_counter() - t0) * 1000, None
    except Exception as exc:  # noqa: BLE001 - every failure becomes a readable line for the operator
        return False, _err(exc), (time.perf_counter() - t0) * 1000, None


def _report_line(r: dict) -> str:
    parts = []
    if r.get("gcode_state"):
        parts.append(f"state {r['gcode_state']}")
    if r.get("nozzle_temper") is not None:
        parts.append(f"nozzle {round(float(r['nozzle_temper']))} °C")
    if r.get("bed_temper") is not None:
        parts.append(f"bed {round(float(r['bed_temper']))} °C")
    return " · ".join(parts) or "report received"


async def test_bambu(t: ConnectionTest) -> list[dict]:
    if not (t.model and t.serial and t.access_code):
        raise HTTPException(422, "a Bambu Lab printer needs its model, serial number and access code")
    host = await lan_only(t.host)
    ftp_host = await lan_only(t.ftp_host or t.host)
    cam_host = await lan_only(t.camera_host or t.host)
    cam_port = t.camera_port or (322 if t.model == "x1" else 6000)

    reach, login_res, ftp, cam = await asyncio.gather(
        _timed(tcp(host, t.mqtt_port)),
        _timed(bambu_report(host, t.mqtt_port, t.tls, t.serial, t.access_code)),
        _timed(tcp(ftp_host, t.ftp_port, use_tls=True)),
        _timed(tcp(cam_host, cam_port, use_tls=True) if t.model == "x1" else p1_camera(cam_host, cam_port, t.access_code)),
    )
    checks = [_check("network", f"Network · {host}:{t.mqtt_port}", reach[0], reach[1], ms=reach[2])]
    ok, detail, ms, value = login_res
    if ok:
        login, report = value
        checks.append(_check("login", "Login (access code)", True, login, ms=ms))
        checks.append(_check("telemetry", "Telemetry report", True, _report_line(report), ms=ms))
    else:
        refused = "access code" in detail
        no_report = detail.startswith("logged in")
        checks.append(_check("login", "Login (access code)", no_report, "logged in as bblp" if no_report else detail, ms=ms))
        checks.append(_check("telemetry", "Telemetry report", False,
                             "no report: check the serial number" if no_report else
                             ("skipped: the login failed" if refused or reach[0] else "skipped: the printer is unreachable"), ms=ms))
    checks.append(_check("upload", f"File upload (FTPS) · port {t.ftp_port}", ftp[0], ftp[1], ms=ftp[2]))
    label = f"Camera (RTSPS) · port {cam_port}" if t.model == "x1" else f"Camera · port {cam_port}"
    checks.append(_check("camera", label, cam[0], cam[1], optional=True, ms=cam[2]))
    return checks


async def test_moonraker(t: ConnectionTest) -> list[dict]:
    host = await lan_only(t.host)
    port = t.port or 7125
    headers = {"X-Api-Key": t.api_key} if t.api_key else {}
    base = f"http://{host}:{port}"
    reach = await _timed(tcp(host, port))
    checks = [_check("network", f"Network · {host}:{port}", reach[0], reach[1], ms=reach[2])]
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=headers) as http:
        async def info() -> str:
            r = await http.get(f"{base}/server/info")
            if r.status_code in (401, 403):
                raise Refused("Moonraker asks for an API key (Moonraker settings, or leave it to trusted clients)")
            r.raise_for_status()
            res = r.json().get("result", {})
            return f"Moonraker {res.get('moonraker_version', '?')} · Klipper {res.get('klippy_state', '?')}"

        async def telemetry() -> str:
            r = await http.get(f"{base}/printer/objects/query", params={"extruder": "", "heater_bed": "", "print_stats": ""})
            r.raise_for_status()
            st = r.json().get("result", {}).get("status", {})
            parts = [f"state {st.get('print_stats', {}).get('state', '?')}"]
            if "extruder" in st:
                parts.append(f"nozzle {round(float(st['extruder'].get('temperature', 0)))} °C")
            if "heater_bed" in st:
                parts.append(f"bed {round(float(st['heater_bed'].get('temperature', 0)))} °C")
            return " · ".join(parts)

        async def camera() -> str:
            last: Exception | None = None
            for url in (f"{base}/webcam/?action=snapshot", f"http://{host}/webcam/?action=snapshot"):
                try:
                    r = await http.get(url)
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/jpeg"):
                        return f"snapshot received ({len(r.content) // 1024} KB) · {url.split('//', 1)[1].split('/')[0]}"
                    last = RuntimeError(f"HTTP {r.status_code} {r.headers.get('content-type', '')}")
                except httpx.HTTPError as exc:
                    last = exc
            raise last or RuntimeError("no webcam")

        api, tel, cam = await asyncio.gather(_timed(info()), _timed(telemetry()), _timed(camera()))
    checks.append(_check("api", "Moonraker API", api[0], api[1], ms=api[2]))
    tel_detail = tel[1] if tel[0] or api[0] else "skipped: the API is unreachable"
    checks.append(_check("telemetry", "Telemetry (temperatures)", tel[0], tel_detail, ms=tel[2]))
    checks.append(_check("camera", "Camera (Crowsnest webcam)", cam[0], cam[1], optional=True, ms=cam[2]))
    return checks


async def test_generic(t: ConnectionTest) -> list[dict]:
    host = await lan_only(t.host)
    port = t.port or 80
    reach = await _timed(tcp(host, port))
    return [_check("network", f"Network · {host}:{port}", reach[0], reach[1], ms=reach[2])]


TESTS = {"bambu": test_bambu, "moonraker": test_moonraker, "generic": test_generic}


@router.post("/test-connection")
async def test_connection(body: ConnectionTest, request: Request, claims: dict = Depends(auth.OPERATOR_CHANGE)) -> dict:
    checks = await TESTS[body.vendor](body)
    ok = all(c["ok"] for c in checks if not c["optional"])
    # Audited without the access code or API key: who tested which address, and the outcome.
    await request.app.state.db.execute(
        "INSERT INTO gateway.audit_log (command_id, actor, subject, payload, outcome) VALUES (%s, %s, %s, %s, 'done')",
        (f"printers.test_connection-{uuid.uuid4().hex}", claims["sub"], "printers.test_connection",
         Jsonb({"vendor": body.vendor, "model": body.model, "host": body.host, "ok": ok})))
    return {"ok": ok, "vendor": body.vendor, "checks": checks, "tested_at": time.time()}
