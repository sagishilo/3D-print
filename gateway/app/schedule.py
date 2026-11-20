"""Schedule (Wave 2 / W2-C): the print timeline and printer maintenance, read-only.

GET /api/v1/schedule forecasts the next hours per printer with the fleet agent's own policy (printfarm.agents.fleet
.policy): the running plates, then what each printer will take next and why, plus the queue with holds, pins and the
manual order, and the filament no printer has loaded (a spool-change suggestion). Changes go through the jobs.control
/ printers.policy / maintenance.* commands.

GET /api/v1/printers/{id}/maintenance: counters, the plate-swap policy, each task's state and the log.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from printfarm.agents.fleet import maintenance as mt
from printfarm.agents.fleet.policy import Block, QPrinter, Window, forecast, order_for, qjob, quiet_now, window_from
from printfarm.contracts.config_keys import CONFIG_DEFAULTS

from . import auth
from .commands import PRINTER_ID

router = APIRouter(dependencies=[Depends(auth.require_auth)])
TZ = "Asia/Jerusalem"
HORIZON_H = 24
WINDOW_KEYS = ("fleet.quiet_hours", "fleet.quiet_from", "fleet.quiet_until", "fleet.overnight_from", "fleet.long_job_hours")


async def window(bus) -> Window:
    values = dict(CONFIG_DEFAULTS)
    for k in WINDOW_KEYS:
        try:
            raw = await bus.kv_get("config", k)
        except Exception:  # noqa: BLE001 - NATS briefly away: the defaults
            raw = None
        if raw is not None:
            values[k] = json.loads(raw)
    return window_from(values.__getitem__)


def task_status(t: dict, counters: mt.Counters, now: datetime) -> mt.TaskStatus:
    return mt.status(mt.Task(t["printer_id"], t["task_id"], t["label"], t["every_hours"], t["every_plates"],
                             t["every_days"], t["block_when_overdue"], t["last_done_at"], t["last_done_seconds"],
                             t["last_done_plates"]), counters, now)


async def maintenance_rows(db, now: datetime, printer_id: str | None = None) -> list[dict]:
    """Every active task with its state (all printers, or one)."""
    rows = await db.fetchall(
        "SELECT t.*, p.print_seconds_total, p.plates_total FROM fleet.maintenance_tasks t "
        "JOIN fleet.printers p USING (printer_id) WHERE NOT t.archived AND (%s::text IS NULL OR t.printer_id = %s) "
        "ORDER BY t.printer_id, t.task_id", (printer_id, printer_id))
    out = []
    for t in rows:
        st = task_status(t, mt.Counters(int(t["print_seconds_total"]), int(t["plates_total"])), now)
        out.append({"printer_id": t["printer_id"], "task_id": t["task_id"], "label": t["label"],
                    "every_hours": t["every_hours"], "every_plates": t["every_plates"], "every_days": t["every_days"],
                    "block_when_overdue": t["block_when_overdue"], "last_done_at": t["last_done_at"],
                    "state": st.state, "used": st.used, "left": st.left, "blocks": st.blocks})
    return out


def why_not(p: dict, blocked: set[str]) -> str:
    """Why a printer takes no new plate now (a code the dashboard translates); "" = it can. "bed": planned as if
    someone clears it now."""
    if not p["online"]:
        return "offline"
    if p["calibrating"]:
        return "calibrating"
    if p["printer_id"] in blocked:
        return "maintenance"
    if not p["busy"] and not p["bed_clear"]:
        return "stack" if p.get("auto_eject") and not p.get("plates_left") else "bed"
    return ""


def build(printers: list[dict], jobs: list[dict], running: list[dict], blocked: set[str], now: datetime, w: Window,
          tz, horizon_h: float = HORIZON_H) -> dict:
    """The timeline (pure: rows in, JSON out)."""
    by_plate: dict[str, list[str]] = {}
    for r in running:
        by_plate.setdefault(r["plate_id"], []).append(r["job_id"])
    blocks, free_at = [], {}
    seen = set()
    for r in running:
        if r["plate_id"] in seen:
            continue
        seen.add(r["plate_id"])
        start = r["started_at"] or now
        end = max(start + timedelta(minutes=float(r["est_minutes"])), now + timedelta(minutes=1))
        blocks.append(Block(r["printer_id"], start, end, tuple(by_plate[r["plate_id"]]), {}, running=True,
                            note=r["status"]))
        free_at[r["printer_id"]] = max(free_at.get(r["printer_id"], now), end)
    qprinters = []
    for p in printers:
        reason = why_not(p, blocked)
        # A busy printer is free after its plate; one waiting for a person is planned as if they come now.
        qprinters.append(QPrinter(p["printer_id"], p["material"], p["color"], (p["bed_w"], p["bed_d"]),
                                  free_at.get(p["printer_id"], now), available=reason in ("", "bed"),
                                  why_not=reason))
    queued = [qjob(j) for j in jobs]
    fc = forecast(qprinters, queued, now, w, tz, running=blocks, horizon_h=horizon_h)
    jobs_by_id = {j["job_id"]: j for j in jobs}
    planned: dict[str, dict] = {}
    for pid, lane in fc.lanes.items():
        for b in lane:
            if b.running:
                continue
            for jid in b.jobs:
                planned[jid] = {"printer_id": pid, "start": b.start.isoformat(), "reason": b.reasons.get(jid, "")}
    loaded = {(p.material, p.color) for p in qprinters}
    queue = []
    for j in sorted(queued, key=lambda q: (q.hold, q.rank is None, q.rank or 0, -q.priority,
                                           q.due_by or datetime.max.replace(tzinfo=UTC), q.created_at or now)):
        plan = planned.get(j.job_id)
        if j.hold:
            reason = "held"
        elif plan:
            reason = plan["reason"]
        elif (j.material, j.color) not in loaded:
            reason = f"no printer has {j.material} {j.color} loaded"
        elif j.pinned_printer and j.pinned_printer not in {p.printer_id for p in qprinters}:
            reason = f"pinned to {j.pinned_printer}, which is gone"
        else:
            p = next((p for p in qprinters if (p.material, p.color) == (j.material, j.color)), None)
            ranked = order_for(p, [j], now, w, tz) if p else []
            reason = (ranked[0].reason + " · later") if ranked else "waits"
        row = jobs_by_id[j.job_id]
        queue.append({"job_id": j.job_id, "order_id": j.order_id, "product_type": row.get("product_type"),
                      "material": j.material, "color": j.color, "est_minutes": j.est_minutes, "priority": j.priority,
                      "due_by": j.due_by, "hold": j.hold, "pinned_printer": j.pinned_printer, "rank": j.rank,
                      "planned_printer": plan["printer_id"] if plan else None,
                      "planned_start": plan["start"] if plan else None, "reason": reason})
    lanes = []
    for p in printers:
        q = next(x for x in qprinters if x.printer_id == p["printer_id"])
        lanes.append({
            "printer_id": p["printer_id"], "material": p["material"], "color": p["color"], "online": p["online"],
            "available": q.available, "why_not": q.why_not, "auto_eject": p.get("auto_eject", False),
            "plate_stack": p.get("plate_stack", 0), "plates_left": p.get("plates_left", 0),
            "blocks": [{"start": b.start.isoformat(), "end": b.end.isoformat(), "running": b.running, "note": b.note,
                        "jobs": [{"job_id": jid, "order_id": (jobs_by_id.get(jid) or {}).get("order_id"),
                                  "reason": b.reasons.get(jid, "")} for jid in b.jobs]}
                       for b in fc.lanes.get(p["printer_id"], [])],
        })
    return {"now": now.isoformat(), "horizon_h": horizon_h, "lanes": lanes, "queue": queue, "unplaced": fc.unplaced,
            "window": {"overnight_from": w.overnight_from, "quiet_enabled": w.quiet_enabled, "quiet_from": w.quiet_from,
                       "quiet_until": w.quiet_until, "long_job_hours": w.long_job_minutes / 60,
                       "quiet_now": quiet_now(now, w, tz)}}


async def snapshot(request: Request) -> dict:
    db, bus, now = request.app.state.db, request.app.state.bus, datetime.now(UTC)
    printers = await db.fetchall(
        "SELECT printer_id, material, color, bed_w, bed_d, online, busy, bed_clear, calibrating, auto_eject, "
        "plate_stack, plates_left FROM fleet.printers ORDER BY printer_id")
    jobs = await db.fetchall("SELECT * FROM fleet.jobs WHERE status = 'queued' ORDER BY created_at LIMIT 500")
    running = await db.fetchall(
        "SELECT p.plate_id, p.printer_id, p.status, p.started_at, p.est_minutes, j.job_id FROM fleet.plates p "
        "JOIN fleet.jobs j ON j.plate_id = p.plate_id WHERE p.status IN ('scheduled', 'printing') ORDER BY p.plate_id, j.job_id")
    blocked = {t["printer_id"] for t in await maintenance_rows(db, now) if t["blocks"]}
    return build(printers, jobs, running, blocked, now, await window(bus), ZoneInfo(TZ))


@router.get("/api/v1/schedule")
async def schedule(request: Request):
    return await snapshot(request)


@router.get("/api/v1/printers/{printer_id}/maintenance")
async def printer_maintenance(printer_id: str, request: Request):
    if not PRINTER_ID.match(printer_id):
        raise HTTPException(400, "bad printer id")
    db, now = request.app.state.db, datetime.now(UTC)
    p = await db.fetchrow(
        "SELECT printer_id, auto_eject, plate_stack, plates_left, print_seconds_total, plates_total, grams_total "
        "FROM fleet.printers WHERE printer_id = %s", (printer_id,))
    if p is None:
        raise HTTPException(404, "no such printer")
    log = await db.fetchall("SELECT l.task_id, coalesce(t.label, l.task_id) AS label, l.done_by, l.note, l.at "
                            "FROM fleet.maintenance_log l LEFT JOIN fleet.maintenance_tasks t USING (printer_id, task_id) "
                            "WHERE l.printer_id = %s ORDER BY l.at DESC LIMIT 20", (printer_id,))
    return {"printer_id": printer_id,
            "counters": {"print_hours": round(int(p["print_seconds_total"]) / 3600, 1), "plates": p["plates_total"],
                         "kg": round(float(p["grams_total"]) / 1000, 2)},
            "policy": {"auto_eject": p["auto_eject"], "plate_stack": p["plate_stack"], "plates_left": p["plates_left"]},
            "tasks": await maintenance_rows(db, now, printer_id), "log": log}
