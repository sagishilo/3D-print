"""Printers for the dashboard: fleet table + latest live state + camera, printer details (history, calibration),
the fleet event log, and the last hour of telemetry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from printfarm.contracts.calibration import routines_for

from . import auth
from .commands import PRINTER_ID
from .jsread import as_item, decode, read_range

router = APIRouter(prefix="/api/v1/printers", dependencies=[Depends(auth.require_auth)])
PROTOCOL = {"bambu": "Bambu MQTT", "moonraker": "Klipper"}
SAMPLE_FIELDS = ("observed_at", "status", "plate_id", "progress", "layer", "total_layers", "nozzle_temp",
                 "nozzle_target", "bed_temp", "bed_target", "remaining_s", "error_code")
PRINTER_COLUMNS = ("printer_id, vendor, model, simulated, bed_w, bed_d, nozzle_mm, material, color, online, busy, "
                   "bed_clear, calibrating, updated_at, max_z, ams_slots, loaded")
LOG_SUBJECTS = ("evt.printer.>", "evt.fleet.plate.>")
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


async def _cameras(request: Request) -> set[str]:
    try:
        r = await request.app.state.http.get("/api/streams", timeout=3)
        return set(r.json()) if r.status_code == 200 else set()
    except Exception:  # noqa: BLE001 - cameras are optional for this list
        return set()


async def _live_state(bus, printer_id: str) -> dict | None:
    raw = await bus.kv_get("printer_status", printer_id)
    env = decode(raw) if raw else None
    return env.get("data") if isinstance(env, dict) else None


async def _running_calibrations(db, printer_id: str | None = None) -> dict[str, dict]:
    rows = await db.fetchall(
        "SELECT calibration_id, printer_id, routine, requested_by, started_at FROM telemetry.calibrations "
        "WHERE status = 'running'" + (" AND printer_id = %s" if printer_id else ""), (printer_id,) if printer_id else ())
    return {r["printer_id"]: r for r in rows}


def _printer(r: dict, state: dict | None, cams: set[str], calibration: dict | None) -> dict:
    return {
        "id": r["printer_id"], "vendor": r["vendor"], "protocol": PROTOCOL.get(r["vendor"], r["vendor"]),
        "model": r["model"], "simulated": r["simulated"], "bed_mm": [r["bed_w"], r["bed_d"]], "nozzle_mm": r["nozzle_mm"],
        "material": r["material"], "color": r["color"], "online": r["online"], "busy": r["busy"],
        "bed_clear": r["bed_clear"], "updated_at": r["updated_at"], "state": state,
        "camera": r["printer_id"] in cams, "calibration": calibration,
        "max_z_mm": r.get("max_z") or None, "ams_slots": r.get("ams_slots") or 0,
        "ams_filaments": [f"{m} {c}" for m, c in (r.get("loaded") or [])],
    }


@router.get("")
async def printers(request: Request):
    bus, db = request.app.state.bus, request.app.state.db
    rows = await db.fetchall(f"SELECT {PRINTER_COLUMNS} FROM fleet.printers ORDER BY printer_id")
    cams, running = await _cameras(request), await _running_calibrations(db)
    return {"printers": [_printer(r, await _live_state(bus, r["printer_id"]), cams, running.get(r["printer_id"]))
                         for r in rows]}


# ---------------------------------------------------------------- fleet event log (before /{printer_id})


def severity_of(kind: str, data: dict) -> str:
    """How serious a printer / plate event is for the operator."""
    if kind == "printer.failure.detected":
        return data.get("severity") if data.get("severity") in SEVERITY_RANK else "warning"
    if kind == "printer.pause.unconfirmed":
        return "critical"
    if kind in ("printer.print.finished", "fleet.plate.finished") and not data.get("succeeded", True):
        return "warning"
    if kind == "printer.connection.changed" and not data.get("online", True):
        return "warning"
    if kind == "printer.calibration.finished" and not data.get("succeeded") and data.get("started", True):
        return "warning"
    return "info"


@router.get("/log")
async def log(request: Request, printer: str | None = None, min_severity: str = Query("info", pattern="^(info|warning|critical)$"),
              hours: int = Query(24, ge=1, le=168), limit: int = Query(300, ge=1, le=1000)):
    """Printer and plate events of the last ``hours`` (the EVENTS stream keeps 30 days), newest first."""
    if printer is not None and not PRINTER_ID.match(printer):
        raise HTTPException(400, "bad printer id")
    since = datetime.now(UTC) - timedelta(hours=hours)
    js, floor = request.app.state.bus.js, SEVERITY_RANK[min_severity]
    items = []
    for subject in LOG_SUBJECTS:
        for msg in await read_range(js, subject, since=since, limit=5000):
            item = as_item(msg)
            env = item["data"] if isinstance(item["data"], dict) else {}
            data = env.get("data") if isinstance(env.get("data"), dict) else {}
            if printer and data.get("printer_id") != printer:
                continue
            kind = str(env.get("type", "")).removeprefix("io.printfarm.").rsplit(".v", 1)[0]
            severity = severity_of(kind, data)
            if SEVERITY_RANK[severity] < floor:
                continue
            items.append({**item, "severity": severity, "printer_id": data.get("printer_id"),
                          "time": env.get("time"), "source": env.get("source")})
    items.sort(key=lambda i: (str(i["time"] or ""), i["seq"]), reverse=True)
    return {"since": since, "count": len(items), "items": items[:limit]}


# ---------------------------------------------------------------- one printer


@router.get("/{printer_id}")
async def printer_detail(printer_id: str, request: Request):
    """Everything the printer details panel shows: state, supported calibrations and recent history."""
    if not PRINTER_ID.match(printer_id):
        raise HTTPException(400, "bad printer id")
    bus, db = request.app.state.bus, request.app.state.db
    row = await db.fetchrow(f"SELECT {PRINTER_COLUMNS} FROM fleet.printers WHERE printer_id = %s", (printer_id,))
    if row is None:
        raise HTTPException(404, f"no printer {printer_id}")
    running = await _running_calibrations(db, printer_id)
    week = datetime.now(UTC) - timedelta(days=7)
    stats = await db.fetchrow(
        "SELECT count(*) AS prints, count(*) FILTER (WHERE succeeded) AS succeeded, "
        "count(*) FILTER (WHERE succeeded = false) AS failed, "
        "coalesce(sum(extract(epoch FROM finished_at - started_at)) FILTER (WHERE finished_at IS NOT NULL), 0) "
        "AS print_seconds FROM telemetry.print_sessions WHERE printer_id = %s AND started_at >= %s",
        (printer_id, week)) or {}
    return {
        "printer": _printer(row, await _live_state(bus, printer_id), await _cameras(request), running.get(printer_id)),
        "routines": [{"key": r.key, "label": r.label, "help": r.help, "max_minutes": r.max_minutes,
                      "heater": r.heater or None, "default_temp": r.default_temp or None} for r in routines_for(row["vendor"])],
        "calibrations": await db.fetchall(
            "SELECT calibration_id, routine, temperature, requested_by, status, detail, started_at, finished_at "
            "FROM telemetry.calibrations WHERE printer_id = %s ORDER BY started_at DESC LIMIT 20", (printer_id,)),
        "sessions": await db.fetchall(
            "SELECT plate_id, started_at, finished_at, succeeded FROM telemetry.print_sessions "
            "WHERE printer_id = %s ORDER BY started_at DESC LIMIT 20", (printer_id,)),
        "pause_checks": await db.fetchall(
            "SELECT reason, requested_by, confirmed, latency_ms, attempts, final_status, checked_at "
            "FROM telemetry.pause_checks WHERE printer_id = %s ORDER BY checked_at DESC LIMIT 10", (printer_id,)),
        "failures": await db.fetchall(
            "SELECT plate_id, detector, severity, message, action_taken, detected_at FROM telemetry.failures "
            "WHERE printer_id = %s ORDER BY detected_at DESC LIMIT 30", (printer_id,)),
        "stats": {"prints_7d": stats.get("prints", 0), "succeeded_7d": stats.get("succeeded", 0),
                  "failed_7d": stats.get("failed", 0), "print_hours_7d": round(float(stats.get("print_seconds") or 0) / 3600, 1)},
    }


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
