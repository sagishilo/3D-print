"""System & Agents (read side + DLQ actions): agent health, NATS, Postgres, streams, dead letters, settings."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from nats.js.errors import NotFoundError
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from printfarm import __version__
from printfarm.contracts import Envelope
from printfarm.contracts.config_keys import CONFIG_KEYS

from . import auth
from .config import settings
from .jsread import decode

router = APIRouter(prefix="/api/v1/system", dependencies=[Depends(auth.require_auth)])

# Every agent the platform knows, including the planned ones (shown as "planned" until they report a heartbeat).
AGENTS = (
    {"name": "coordinator", "label": "Coordinator", "detail": "order sagas · approvals · payments", "tier": "core"},
    {"name": "telemetry", "label": "Telemetry", "detail": "printers · verified pauses", "tier": "core"},
    {"name": "fleet", "label": "Fleet Manager", "detail": "plate packing + scheduling", "tier": "core"},
    {"name": "personalization", "label": "Personalization", "detail": "nameplates", "tier": "core"},
    {"name": "inventory", "label": "Inventory", "detail": "filament · reorders · POs", "tier": "core"},
    {"name": "finance", "label": "Finance", "detail": "ledger · VAT", "tier": "core"},
    {"name": "social", "label": "Social Listening", "detail": "captured posts · intent · reply drafts", "tier": "optional"},
    {"name": "trend", "label": "Trend Analyst", "detail": "arrives in phase 10b", "tier": "optional", "planned": True},
)
HEALTHY_WITHIN_S = 25   # heartbeats come every 10 s and expire from the KV bucket after 30 s
DLQ_SCAN_MAX = 1000


async def _heartbeats(bus) -> dict[str, dict]:
    beats = {}
    for key in await bus.kv_keys("heartbeats"):
        raw = await bus.kv_get("heartbeats", key)
        if raw:
            beats[key] = decode(raw)
    return beats


async def consumer_lag(js) -> dict[str, dict]:
    """Per agent: messages waiting in its durable consumers (named "<agent>__<filter>")."""
    out: dict[str, dict] = {}
    for stream in ("EVENTS", "COMMANDS"):
        try:
            infos = await js.consumers_info(stream)
        except Exception:  # noqa: BLE001 - stream missing or NATS busy: report what we have
            continue
        for ci in infos:
            agent, sep, _ = (ci.name or "").partition("__")
            if not sep:
                continue  # ephemeral consumers (dashboard feeds)
            entry = out.setdefault(agent, {"pending": 0, "ack_pending": 0, "consumers": 0})
            entry["pending"] += ci.num_pending or 0
            entry["ack_pending"] += ci.num_ack_pending or 0
            entry["consumers"] += 1
    return out


@router.get("/agents")
async def agents(request: Request):
    bus = request.app.state.bus
    beats, lag, now = await _heartbeats(bus), await consumer_lag(bus.js), datetime.now(UTC)
    rows = []
    for a in (*AGENTS, *({"name": n, "label": n, "detail": "", "tier": "other"} for n in beats if n not in
                         {x["name"] for x in AGENTS})):
        beat = beats.get(a["name"])
        age = None
        if beat and isinstance(beat, dict) and beat.get("at"):
            try:
                age = max((now - datetime.fromisoformat(beat["at"])).total_seconds(), 0.0)
            except (TypeError, ValueError):
                age = None
        if age is not None:
            status = "healthy" if age <= HEALTHY_WITHIN_S else "late"
        else:
            status = "planned" if a.get("planned") else "down"
        q = lag.get(a["name"])
        rows.append({
            "name": a["name"], "label": a["label"], "detail": a["detail"], "tier": a["tier"], "status": status,
            "heartbeat_age_s": round(age, 1) if age is not None else None,
            "version": (beat or {}).get("version"), "uptime_s": (beat or {}).get("uptime_s"),
            "host": (beat or {}).get("host"),
            "queue": (q["pending"] + q["ack_pending"]) if q else None,
        })
    return {"agents": rows}


async def _nats(request: Request, path: str) -> dict | None:
    try:
        r = await request.app.state.nats_http.get(path, timeout=3)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


@router.get("/overview")
async def overview(request: Request):
    state = request.app.state
    js = state.bus.js
    varz, jsz = await _nats(request, "/varz"), await _nats(request, "/jsz")
    msgs_per_s = None
    if varz:
        total, t = (varz.get("in_msgs", 0) or 0) + (varz.get("out_msgs", 0) or 0), time.monotonic()
        prev = getattr(state, "varz_prev", None)
        if prev and t - prev[0] >= 1:
            msgs_per_s = round(max(total - prev[1], 0) / (t - prev[0]), 1)
        if prev is None or t - prev[0] >= 1:
            state.varz_prev = (t, total)
    pg = None
    try:
        pg = await state.db.fetchrow(
            "SELECT split_part(version(), ' ', 2) AS version, "
            "(SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS connections, "
            "current_setting('max_connections')::int AS max_connections, "
            "(SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND query_start < now() - interval '5 seconds') "
            "AS slow_queries")
    except Exception:  # noqa: BLE001
        pg = None
    lag = await consumer_lag(js)
    worst = max(lag.items(), key=lambda kv: kv[1]["pending"] + kv[1]["ack_pending"], default=(None, None))
    try:
        dlq = (await js.stream_info("DLQ")).state.messages
    except Exception:  # noqa: BLE001
        dlq = None
    try:
        go2rtc = (await state.http.get("/api", timeout=3)).json()
        go2rtc_ok = True
    except Exception:  # noqa: BLE001
        go2rtc, go2rtc_ok = {}, False
    return {
        "nats": {
            "ok": state.bus.nc.is_connected and varz is not None,
            "version": (varz or {}).get("version"), "uptime": (varz or {}).get("uptime"),
            "connections": (varz or {}).get("connections"), "msgs_per_s": msgs_per_s,
            "mem_bytes": (varz or {}).get("mem"),
            "jetstream": {k: (jsz or {}).get(k) for k in ("streams", "consumers", "messages", "bytes")},
        },
        "postgres": {"ok": pg is not None, **(pg or {})},
        "go2rtc": {"ok": go2rtc_ok, "version": go2rtc.get("version") if isinstance(go2rtc, dict) else None},
        "gateway": {"ok": True, "version": __version__, "ws_clients": getattr(state, "ws_clients", 0),
                    "uptime_s": int(time.monotonic() - getattr(state, "started", time.monotonic()))},
        "consumer_lag": {"total": sum(v["pending"] + v["ack_pending"] for v in lag.values()),
                         "max_agent": worst[0], "max": (worst[1]["pending"] + worst[1]["ack_pending"]) if worst[1] else 0},
        "dlq": {"messages": dlq},
    }


@router.get("/connections")
async def connections(request: Request):
    connz = await _nats(request, "/connz?limit=256") or {}
    keep = ("cid", "name", "ip", "port", "uptime", "subscriptions", "in_msgs", "out_msgs", "rtt", "lang", "version")
    return {"connections": [{k: c.get(k) for k in keep} for c in connz.get("connections", [])],
            "total": connz.get("num_connections")}


@router.get("/streams")
async def streams(request: Request):
    out = []
    for si in await request.app.state.bus.js.streams_info():
        c, s = si.config, si.state
        out.append({"name": c.name, "subjects": c.subjects, "storage": str(getattr(c.storage, "value", c.storage)),
                    "max_age_s": c.max_age, "messages": s.messages, "bytes": s.bytes, "consumers": s.consumer_count,
                    "first_seq": s.first_seq, "last_seq": s.last_seq})
    return {"streams": sorted(out, key=lambda x: x["name"])}


# ---------------------------------------------------------------- dead letters


def _dlq_item(msg) -> dict:
    env = decode(msg.data)
    d = env.get("data", {}) if isinstance(env, dict) else {}
    return {"seq": msg.seq, "time": env.get("time") if isinstance(env, dict) else None,
            "original_subject": d.get("original_subject"), "original_id": d.get("original_id"),
            "consumer": d.get("consumer"), "error": d.get("error"), "deliveries": d.get("deliveries"),
            "original": d.get("original")}


@router.get("/dlq")
async def dlq(request: Request, limit: int = 50):
    js = request.app.state.bus.js
    st = (await js.stream_info("DLQ")).state
    items, seq, scanned = [], st.last_seq, 0
    while seq >= max(st.first_seq, 1) and len(items) < min(max(limit, 1), 200) and scanned < DLQ_SCAN_MAX:
        scanned += 1
        try:
            items.append(_dlq_item(await js.get_msg("DLQ", seq)))
        except NotFoundError:
            pass  # discarded or replayed
        seq -= 1
    return {"total": st.messages, "items": items}


async def _dlq_get(js, seq: int):
    try:
        return await js.get_msg("DLQ", seq)
    except NotFoundError as exc:
        raise HTTPException(404, f"no dead letter #{seq}") from exc


async def _audit(request: Request, claims: dict, subject: str, payload: dict) -> None:
    await request.app.state.db.execute(
        "INSERT INTO gateway.audit_log (command_id, actor, subject, payload, outcome) VALUES (%s, %s, %s, %s, 'done')",
        (f"{subject}-{uuid.uuid4().hex}", claims["sub"], subject, Jsonb(payload)))


@router.post("/dlq/{seq}/replay", status_code=202)
async def dlq_replay(seq: int, request: Request, claims: dict = Depends(auth.OWNER_CHANGE)):
    """Publish the dead-lettered message again. Consumers that already handled it skip it (inbox), the one that
    failed gets another chance; the JetStream de-duplication id is new so the replay is not dropped."""
    js = request.app.state.bus.js
    item = _dlq_item(await _dlq_get(js, seq))
    try:
        env = Envelope.model_validate(item["original"])
        subject = env.nats_subject()
    except (ValidationError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"dead letter #{seq} holds no valid message: {exc}") from exc
    ack = await js.publish(subject, env.to_bytes(), headers={"Nats-Msg-Id": f"{env.id}:replay:{seq}"})
    await _audit(request, claims, "dlq.replay", {"dlq_seq": seq, "subject": subject, "message_id": env.id})
    await js.delete_msg("DLQ", seq)
    return {"replayed": True, "subject": subject, "stream": ack.stream, "seq": ack.seq}


@router.post("/dlq/{seq}/discard")
async def dlq_discard(seq: int, request: Request, claims: dict = Depends(auth.OWNER_CHANGE)):
    js = request.app.state.bus.js
    item = _dlq_item(await _dlq_get(js, seq))
    await _audit(request, claims, "dlq.discard", {"dlq_seq": seq, "subject": item["original_subject"],
                                                  "message_id": item["original_id"]})
    await js.delete_msg("DLQ", seq)
    return {"discarded": True}


# ---------------------------------------------------------------- automation settings


@router.get("/config")
async def config(request: Request):
    bus, db = request.app.state.bus, request.app.state.db
    last = {r["key"]: r for r in await db.fetchall(
        "SELECT DISTINCT ON (key) key, value, changed_by, at FROM coordinator.config_changes ORDER BY key, at DESC")}
    out = []
    for k in CONFIG_KEYS:
        raw = await bus.kv_get("config", k.key)
        change = last.get(k.key)
        out.append({"key": k.key, "label": k.label, "help": k.help, "default": k.default, "phase": k.phase,
                    "minimum": k.minimum, "maximum": k.maximum,
                    "value": json.loads(raw) if raw is not None else k.default, "is_default": raw is None,
                    "changed_by": change["changed_by"] if change else None,
                    "changed_at": change["at"] if change else None,
                    "changed_value": change.get("value") if change else None})   # what that last change set
    return {"settings": out}


# ---------------------------------------------------------------- backups (status.json from the backup service)
def _age_s(stamp: str, now: float) -> float | None:
    try:
        return now - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def backup_health(st: dict | None, now: float) -> dict:
    """What the dashboard shows: is there a recent local backup, and (when set up) a recent off-site copy?
    Stale = older than two backup intervals (the same rule as the backup service's own health check)."""
    if not st:
        return {"known": False, "state": "unknown", "problems": ["No backup status yet: is the backup service running?"]}
    limit = 2 * float(st.get("interval_s") or 86400)
    problems: list[str] = []
    local_age = _age_s(st.get("local_ok_at") or "", now)
    if local_age is None:
        problems.append("No local backup has succeeded yet.")
    elif local_age > limit:
        problems.append(f"The last local backup is {int(local_age // 3600)} h old.")
    if st.get("local_error") and (st.get("local_error_at") or "") > (st.get("local_ok_at") or ""):
        problems.append(f"The last local backup failed: {st['local_error']}")
    offsite_age = _age_s(st.get("offsite_ok_at") or "", now)
    if st.get("offsite_enabled"):
        if offsite_age is None:
            problems.append("No off-site copy has succeeded yet.")
        elif offsite_age > limit:
            problems.append(f"The last off-site copy is {int(offsite_age // 3600)} h old.")
        if st.get("offsite_error") and (st.get("offsite_error_at") or "") > (st.get("offsite_ok_at") or ""):
            problems.append(f"The last off-site copy failed: {st['offsite_error']}")
    elif st.get("offsite_error"):
        problems.append(st["offsite_error"])
    state = "problem" if problems else ("ok" if st.get("offsite_enabled") else "local_only")
    return {**st, "known": True, "state": state, "problems": problems,
            "local_age_s": local_age, "offsite_age_s": offsite_age}


@router.get("/backups")
async def backups():
    """Last local and off-site backup, from the backup service's status.json (README "Backups")."""
    try:
        with open(settings.backup_status_file, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = None
    return backup_health(st, time.time())


# ---------------------------------------------------------------- audit log (owner): who did what, when
AUDIT_PAGE = 100


@router.get("/audit", dependencies=[Depends(auth.OWNER)])
async def audit(request: Request, actor: str | None = None, action: str | None = None, outcome: str | None = None,
                since: str | None = None, until: str | None = None, before_id: int | None = None, limit: int = AUDIT_PAGE):
    """Newest first. ``action`` matches the start of the subject (``users`` matches users.create, users.update...),
    ``since`` / ``until`` are dates (YYYY-MM-DD, until inclusive), ``before_id`` pages back."""
    where, params = [], []
    if actor:
        where.append("actor = %s")
        params.append(actor)
    if action:
        where.append("subject LIKE %s")
        params.append(action.replace("%", "").replace("_", r"\_") + "%")
    if outcome == "failed":
        where.append("(outcome LIKE 'failed%%' OR outcome IN ('refused', 'locked out'))")
    for value, op, shift in ((since, ">=", ""), (until, "<", " + interval '1 day'")):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"dates are YYYY-MM-DD, not {value}") from None
            where.append(f"created_at {op} (%s::date{shift}) AT TIME ZONE 'Asia/Jerusalem'")
            params.append(value)
    if before_id:
        where.append("id < %s")
        params.append(before_id)
    limit = max(1, min(limit, 500))
    rows = await request.app.state.db.fetchall(
        "SELECT id, created_at, actor, subject, outcome, payload FROM gateway.audit_log"
        + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT %s", (*params, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    return {"entries": rows, "next_before_id": rows[-1]["id"] if more and rows else None}


@router.get("/audit/facets", dependencies=[Depends(auth.OWNER)])
async def audit_facets(request: Request):
    """The people and the kinds of action in the log (the audit screen's filter lists)."""
    db = request.app.state.db
    actors = await db.fetchall("SELECT actor, max(created_at) AS last FROM gateway.audit_log GROUP BY actor "
                               "ORDER BY last DESC LIMIT 200")
    actions = await db.fetchall("SELECT subject AS action, count(*) AS n FROM gateway.audit_log GROUP BY 1 "
                                "ORDER BY 2 DESC LIMIT 200")
    return {"actors": [r["actor"] for r in actors], "actions": [r["action"] for r in actions]}
