"""Printers for the dashboard: fleet table + latest live state + camera, and the last hour of telemetry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import auth
from .commands import PRINTER_ID
from .jsread import decode, read_range

router = APIRouter(prefix="/api/v1/printers", dependencies=[Depends(auth.require_auth)])
PROTOCOL = {"bambu": "Bambu MQTT", "moonraker": "Klipper"}
SAMPLE_FIELDS = ("observed_at", "status", "plate_id", "progress", "layer", "total_layers", "nozzle_temp",
                 "nozzle_target", "bed_temp", "bed_target", "remaining_s", "error_code")


async def _cameras(request: Request) -> set[str]:
    try:
        r = await request.app.state.http.get("/api/streams", timeout=3)
        return set(r.json()) if r.status_code == 200 else set()
    except Exception:  # noqa: BLE001 - cameras are optional for this list
        return set()


@router.get("")
async def printers(request: Request):
    bus, db = request.app.state.bus, request.app.state.db
    rows = await db.fetchall(
        "SELECT printer_id, vendor, model, simulated, bed_w, bed_d, nozzle_mm, material, color, online, busy, "
        "bed_clear, updated_at FROM fleet.printers ORDER BY printer_id")
    states = {}
    for key in await bus.kv_keys("printer_status"):
        raw = await bus.kv_get("printer_status", key)
        env = decode(raw) if raw else None
        if isinstance(env, dict):
            states[key] = env.get("data")
    cams = await _cameras(request)
    return {"printers": [{
        "id": r["printer_id"], "vendor": r["vendor"], "protocol": PROTOCOL.get(r["vendor"], r["vendor"]),
        "model": r["model"], "simulated": r["simulated"], "bed_mm": [r["bed_w"], r["bed_d"]], "nozzle_mm": r["nozzle_mm"],
        "material": r["material"], "color": r["color"], "online": r["online"], "busy": r["busy"],
        "bed_clear": r["bed_clear"], "updated_at": r["updated_at"], "state": states.get(r["printer_id"]),
        "camera": r["printer_id"] in cams,
    } for r in rows]}


def downsample(samples: list, points: int) -> list:
    """At most ``points`` evenly spaced samples; the newest one is always kept."""
    if len(samples) <= points:
        return samples
    step = len(samples) / points
    picked = [samples[int(i * step)] for i in range(points - 1)]
    return [*picked, samples[-1]]


@router.get("/{printer_id}/telemetry")
async def telemetry(printer_id: str, request: Request, minutes: int = Query(60, ge=1, le=60),
                    points: int = Query(240, ge=10, le=1000)):
    """Printer state history from the TELEMETRY stream (kept for one hour), for the replay timeline."""
    if not PRINTER_ID.match(printer_id):
        raise HTTPException(400, "bad printer id")
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    msgs = await read_range(request.app.state.bus.js, f"tlm.{printer_id}.state", since=since, limit=minutes * 60 + 60)
    samples = []
    for m in msgs:
        env = decode(m.data)
        data = env.get("data") if isinstance(env, dict) else None
        if isinstance(data, dict):
            samples.append({k: data.get(k) for k in SAMPLE_FIELDS})
    return {"printer_id": printer_id, "since": since, "count": len(samples), "samples": downsample(samples, points)}
