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
async def dlq_replay(seq: int, request: Request, claims: dict = Depends(auth.require_csrf)):
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
async def dlq_discard(seq: int, request: Request, claims: dict = Depends(auth.require_csrf)):
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
