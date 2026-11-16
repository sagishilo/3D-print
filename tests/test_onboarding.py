"""Printer onboarding: the one-off connection test (gateway/app/onboarding.py).

The probes themselves are replaced here (no printer in CI); the real protocol paths are exercised against the
simulators by the dashboard's browser test (web/e2e/onboarding.spec.ts).
"""

from __future__ import annotations

import asyncio
import ssl

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")
pytest.importorskip("aiomqtt")

import importlib  # noqa: E402

import httpx  # noqa: E402
from gateway_support import csrf, gateway_client, login  # noqa: E402

onboarding = importlib.import_module("app.onboarding")   # importable once gateway_support has put gateway/ on the path

REAL_CLIENT = httpx.AsyncClient
BAMBU = {"vendor": "bambu", "host": "192.168.50.21", "model": "p1", "serial": "01P00A123456789", "access_code": "12345678"}


@pytest.fixture
def client(monkeypatch):
    async def lan(host):   # the tests use private addresses without a real resolver
        return host

    monkeypatch.setattr(onboarding, "lan_only", lan)
    with gateway_client() as c:
        login(c)
        yield c


def post(client, body):
    return client.post("/api/v1/printers/test-connection", json=body, headers=csrf(client))


def by_key(r):
    return {c["key"]: c for c in r.json()["checks"]}


def fake_bambu(monkeypatch, *, login_err=None, cam_err=None, ftp_err=None, down=False):
    calls = {}

    async def tcp(host, port, *, use_tls=False):
        calls.setdefault("tcp", []).append((host, port, use_tls))
        if down:
            raise TimeoutError
        if ftp_err and port in (990, 9901):
            raise ConnectionRefusedError
        return "TLS ok (TLSv1.3)" if use_tls else "port open"

    async def report(host, port, use_tls, serial, code):
        calls["report"] = (host, port, use_tls, serial, code)
        if down:
            raise TimeoutError
        if login_err:
            raise onboarding.Refused(login_err)
        return "logged in as bblp", {"gcode_state": "IDLE", "nozzle_temper": 24.6, "bed_temper": 25.1}

    async def camera(host, port, code):
        calls["camera"] = (host, port, code)
        if cam_err:
            raise onboarding.Refused(cam_err)
        return "picture received (31 KB)"

    monkeypatch.setattr(onboarding, "tcp", tcp)
    monkeypatch.setattr(onboarding, "bambu_report", report)
    monkeypatch.setattr(onboarding, "p1_camera", camera)
    return calls


def test_bambu_printer_passes_every_check(client, monkeypatch):
    calls = fake_bambu(monkeypatch)
    r = post(client, BAMBU)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    checks = by_key(r)
    assert list(checks) == ["network", "login", "telemetry", "upload", "camera"]
    assert checks["telemetry"]["detail"] == "state IDLE · nozzle 25 °C · bed 25 °C"
    assert checks["camera"]["optional"] is True
    # Defaults of a real printer: MQTT over TLS on 8883, FTPS 990, P1 camera on 6000 with the access code.
    assert calls["report"] == ("192.168.50.21", 8883, True, "01P00A123456789", "12345678")
    assert ("192.168.50.21", 990, True) in calls["tcp"]
    assert calls["camera"] == ("192.168.50.21", 6000, "12345678")


def test_x1_camera_is_rtsps_on_322(client, monkeypatch):
    calls = fake_bambu(monkeypatch)
    assert post(client, {**BAMBU, "model": "x1"}).json()["ok"] is True
    assert ("192.168.50.21", 322, True) in calls["tcp"]
    assert "camera" not in calls


def test_wrong_access_code_fails_login_and_skips_telemetry(client, monkeypatch):
    fake_bambu(monkeypatch, login_err="the printer refused the access code")
    r = post(client, BAMBU)
    assert r.json()["ok"] is False
    checks = by_key(r)
    assert checks["network"]["ok"] is True
    assert checks["login"] == {**checks["login"], "ok": False, "detail": "the printer refused the access code"}
    assert checks["telemetry"]["detail"] == "skipped: the login failed"


def test_wrong_serial_logs_in_but_gets_no_report(client, monkeypatch):
    fake_bambu(monkeypatch, login_err="logged in, but no status report came")
    checks = by_key(post(client, BAMBU))
    assert checks["login"]["ok"] is True
    assert checks["telemetry"]["ok"] is False
    assert "serial" in checks["telemetry"]["detail"]


def test_unreachable_printer(client, monkeypatch):
    fake_bambu(monkeypatch, down=True)
    r = post(client, BAMBU)
    checks = by_key(r)
    assert r.json()["ok"] is False
    assert "timed out" in checks["network"]["detail"]
    assert checks["telemetry"]["detail"] == "skipped: the printer is unreachable"


def test_a_camera_problem_does_not_fail_the_test(client, monkeypatch):
    fake_bambu(monkeypatch, cam_err="the camera did not send a picture: wrong access code?")
    r = post(client, BAMBU)
    assert r.json()["ok"] is True
    assert by_key(r)["camera"]["ok"] is False


def test_closed_upload_port_fails(client, monkeypatch):
    fake_bambu(monkeypatch, ftp_err=True)
    r = post(client, BAMBU)
    assert r.json()["ok"] is False
    assert "port is closed" in by_key(r)["upload"]["detail"]


def test_simulator_ports_and_hosts_are_used(client, monkeypatch):
    calls = fake_bambu(monkeypatch)
    body = {**BAMBU, "host": "mosquitto", "mqtt_port": 1883, "tls": False, "ftp_host": "simulator", "ftp_port": 9901,
            "camera_host": "simulator", "camera_port": 6001}
    assert post(client, body).json()["ok"] is True
    assert calls["report"][:3] == ("mosquitto", 1883, False)
    assert ("simulator", 9901, True) in calls["tcp"]
    assert calls["camera"][:2] == ("simulator", 6001)


def test_the_access_code_is_never_kept(client, monkeypatch):
    fake_bambu(monkeypatch)
    post(client, BAMBU)
    audit = [a for a in client.app.state.db.audit.values() if a["subject"] == "printers.test_connection"]
    assert len(audit) == 1
    assert audit[0]["payload"] == {"vendor": "bambu", "model": "p1", "host": "192.168.50.21", "ok": True}
    assert "12345678" not in repr(client.app.state.db.audit)


@pytest.mark.parametrize("bad", [
    {"access_code": "1234567"},           # 7 characters
    {"access_code": "1234 678"},
    {"serial": "x"},
    {"host": "192.168.50.21; rm -rf /"},
    {"model": "x2"},
    {"mqtt_port": 70000},
    {"vendor": "prusa"},
    {"extra": 1},
])
def test_bad_input_is_refused(client, monkeypatch, bad):
    fake_bambu(monkeypatch)
    assert post(client, {**BAMBU, **bad}).status_code == 422


def test_bambu_needs_model_serial_and_code(client, monkeypatch):
    fake_bambu(monkeypatch)
    r = post(client, {"vendor": "bambu", "host": "192.168.50.21"})
    assert r.status_code == 422
    assert "access code" in r.json()["detail"]


def test_needs_login_and_csrf():
    with gateway_client() as anonymous:
        assert anonymous.post("/api/v1/printers/test-connection", json=BAMBU).status_code == 401
        login(anonymous)
        assert anonymous.post("/api/v1/printers/test-connection", json=BAMBU).status_code == 403   # no CSRF header


# ---------------------------------------------------------------- Klipper / Moonraker
def moonraker(monkeypatch, *, api_key_needed=False, webcam=True):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.setdefault("urls", []).append(str(request.url))
        seen["key"] = request.headers.get("x-api-key")
        if api_key_needed and request.headers.get("x-api-key") != "secret":
            return httpx.Response(401)
        if request.url.path == "/server/info":
            return httpx.Response(200, json={"result": {"klippy_state": "ready", "moonraker_version": "v0.9.3"}})
        if request.url.path == "/printer/objects/query":
            return httpx.Response(200, json={"result": {"status": {"extruder": {"temperature": 210.4},
                                                                   "heater_bed": {"temperature": 59.6},
                                                                   "print_stats": {"state": "standby"}}}})
        if request.url.path == "/webcam/" and webcam:
            return httpx.Response(200, content=b"\xff\xd8" + b"x" * 4000, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    monkeypatch.setattr(onboarding.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))

    async def tcp(host, port, *, use_tls=False):
        return "port open"

    monkeypatch.setattr(onboarding, "tcp", tcp)
    return seen


def test_klipper_printer_passes(client, monkeypatch):
    seen = moonraker(monkeypatch)
    r = post(client, {"vendor": "moonraker", "host": "192.168.50.31"})
    assert r.json()["ok"] is True
    checks = by_key(r)
    assert checks["api"]["detail"] == "Moonraker v0.9.3 · Klipper ready"
    assert checks["telemetry"]["detail"] == "state standby · nozzle 210 °C · bed 60 °C"
    assert checks["camera"]["ok"] is True
    assert seen["urls"][0].startswith("http://192.168.50.31:7125/")   # Moonraker's default port


def test_klipper_api_key(client, monkeypatch):
    moonraker(monkeypatch, api_key_needed=True)
    r = post(client, {"vendor": "moonraker", "host": "192.168.50.31"})
    assert r.json()["ok"] is False
    assert "API key" in by_key(r)["api"]["detail"]
    seen = moonraker(monkeypatch, api_key_needed=True)
    assert post(client, {"vendor": "moonraker", "host": "192.168.50.31", "api_key": "secret"}).json()["ok"] is True
    assert seen["key"] == "secret"


def test_klipper_without_webcam_still_passes(client, monkeypatch):
    moonraker(monkeypatch, webcam=False)
    r = post(client, {"vendor": "moonraker", "host": "192.168.50.31", "port": 7125})
    assert r.json()["ok"] is True
    assert by_key(r)["camera"]["ok"] is False


def test_generic_printer_checks_the_port(client, monkeypatch):
    async def tcp(host, port, *, use_tls=False):
        assert (host, port) == ("192.168.50.40", 80)
        return "port open"

    monkeypatch.setattr(onboarding, "tcp", tcp)
    r = post(client, {"vendor": "generic", "host": "192.168.50.40"})
    assert r.json() | {"tested_at": 0} == {"ok": True, "vendor": "generic", "tested_at": 0,
                                         "checks": [{"key": "network", "label": "Network · 192.168.50.40:80", "ok": True,
                                                     "detail": "port open", "optional": False, "ms": r.json()["checks"][0]["ms"]}]}


# ---------------------------------------------------------------- only the local network, real probes
@pytest.mark.parametrize("host", ["8.8.8.8", "127.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1"])
def test_only_lan_addresses_are_probed(host):
    with pytest.raises(onboarding.HTTPException) as exc:
        asyncio.run(onboarding.lan_only(host))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("host", ["192.168.50.21", "10.0.0.5", "172.18.0.4"])
def test_lan_addresses_are_allowed(host):
    assert asyncio.run(onboarding.lan_only(host)) == host


def test_tcp_probe_and_readable_errors():
    async def main():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            assert await onboarding.tcp("127.0.0.1", port) == "port open"
        try:
            await onboarding.tcp("127.0.0.1", port)
        except OSError as exc:
            return onboarding._err(exc)
        return "connected"

    assert "port is closed" in asyncio.run(main())
    assert "timed out" in onboarding._err(TimeoutError())
    assert isinstance(onboarding._tls_context(), ssl.SSLContext)
