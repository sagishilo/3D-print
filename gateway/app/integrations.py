"""Integrations read side (Wave 2 / W2-0): connectors, their sync state and runs, their mappings, and the
notifications outbox. Changes go through commands (integrations.configure / integrations.run, owner only), handled
by the integrations agent. Secrets never come here: only "set / missing" per key, as the agent stored it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from printfarm.agents.integrations.framework import health

from . import auth

router = APIRouter()
OWNER = [Depends(auth.OWNER)]

CONNECTORS_SQL = """
  SELECT c.connector_id, c.kind, c.label, c.enabled, c.mode, c.interval_s, c.secrets_status, c.updated_by, c.updated_at,
         s.last_run_at, s.last_ok_at, s.last_error, s.last_error_at, s.consecutive_failures, s.next_run_at, s.runs, s.items,
         (SELECT count(*) FROM integrations.mappings m WHERE m.connector_id = c.connector_id) AS mapped
  FROM integrations.connectors c LEFT JOIN integrations.sync_state s USING (connector_id)
"""


def _connector(r: dict) -> dict:
    return {**r, "health": health(r["enabled"], int(r["consecutive_failures"] or 0), r["last_ok_at"], r["last_run_at"]),
            "secrets_missing": sorted(k for k, v in (r["secrets_status"] or {}).items() if not v)}


@router.get("/api/v1/integrations", dependencies=OWNER)
async def connectors(request: Request):
    rows = await request.app.state.db.fetchall(CONNECTORS_SQL + " ORDER BY c.kind, c.label")
    return {"connectors": [_connector(r) for r in rows]}


@router.get("/api/v1/integrations/health", dependencies=[Depends(auth.OPERATOR)])
async def connectors_health(request: Request):
    """The System screen's card: each connector's health, no settings and no secrets status."""
    rows = await request.app.state.db.fetchall(CONNECTORS_SQL + " ORDER BY c.kind, c.label")
    out = [{k: _connector(r)[k] for k in ("connector_id", "label", "kind", "enabled", "mode", "health", "last_ok_at",
                                         "last_error", "consecutive_failures", "next_run_at")} for r in rows]
    return {"connectors": out, "down": sum(1 for c in out if c["health"] in ("down", "failing"))}


def _id(connector_id: str) -> str:
    if not (0 < len(connector_id) <= 40 and all(ch.isalnum() or ch in "_-" for ch in connector_id)):
        raise HTTPException(400, "bad connector id")
    return connector_id


@router.get("/api/v1/integrations/{connector_id}/runs", dependencies=OWNER)
async def runs(connector_id: str, request: Request, limit: int = Query(20, ge=1, le=200)):
    return {"runs": await request.app.state.db.fetchall(
        "SELECT id, trigger, started_at, finished_at, ok, items, created, updated, error FROM integrations.runs "
        "WHERE connector_id = %s ORDER BY id DESC LIMIT %s", (_id(connector_id), limit))}


@router.get("/api/v1/integrations/{connector_id}/mappings", dependencies=OWNER)
async def mappings(connector_id: str, request: Request, entity: str | None = None, limit: int = Query(50, ge=1, le=500)):
    db, cid = request.app.state.db, _id(connector_id)
    rows = await db.fetchall(
        "SELECT entity, external_id, internal_id, created_at, updated_at FROM integrations.mappings WHERE connector_id = %s"
        + (" AND entity = %s" if entity else "") + " ORDER BY updated_at DESC LIMIT %s",
        (cid, *((entity,) if entity else ()), limit))
    counts = await db.fetchall("SELECT entity, count(*) AS n FROM integrations.mappings WHERE connector_id = %s GROUP BY 1",
                               (cid,))
    return {"mappings": rows, "counts": {r["entity"]: int(r["n"]) for r in counts}}


@router.get("/api/v1/notifications", dependencies=[Depends(auth.OPERATOR)])
async def notifications(request: Request, days: int = Query(7, ge=1, le=90), claims: dict = Depends(auth.require_auth)):
    """What the platform told (or will tell) people: newest first, only what this role may see."""
    rows = await request.app.state.db.fetchall(
        "SELECT id, topic, severity, title, body, link, min_role, status, channels, attempts, error, created_at, sent_at "
        "FROM notify.outbox WHERE created_at > now() - make_interval(days => %s) ORDER BY id DESC LIMIT 200", (days,))
    mine = [r for r in rows if auth.allows(claims.get("role"), r["min_role"])]
    return {"notifications": mine, "generated_at": datetime.now(UTC).isoformat()}
