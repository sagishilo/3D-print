"""PrintFarm Ops FastAPI gateway (BFF): REST, commands, camera proxy, live feed.

Commands: ``POST /api/v1/commands/{name}`` validates the body against the command's contract (``commands.py``) and
publishes it to JetStream with ``Nats-Msg-Id`` = Idempotency-Key. The stream's acknowledgement means "durably
accepted"; the result arrives later as an event over ``/ws`` (``live.py``). The gateway never waits for an agent.

Routers: auth (login/refresh), live (/ws), system (agents, NATS, DLQ, settings), printers, cameras, approvals.
"""
import json
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from nats.errors import ConnectionClosedError, NoRespondersError, NoServersError
from nats.errors import TimeoutError as NatsTimeout
from nats.js.errors import NoStreamResponseError
from psycopg import OperationalError
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout
from pydantic import ValidationError

from printfarm.bus.nats_bus import NatsBus
from printfarm.contracts.catalog import MAX_UPLOAD_BYTES
from printfarm.store import schema

from . import approvals, auth, cameras, catalog, finance, inventory, live, orders, printers, system
from .commands import COMMANDS, PRINTER_ACTIONS, PRINTER_ID, build_command, command_id_from
from .config import settings
from .db import Db
from .middleware import BodyTooLarge, Guard, RateLimiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pf.gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started = time.monotonic()
    app.state.ws_clients = 0
    app.state.db = Db(settings.database_url)
    await app.state.db.open()
    app.state.bus = await NatsBus.connect(settings.nats_url, name="gateway")  # also ensures the shared JetStream topology
    app.state.http = httpx.AsyncClient(base_url=settings.go2rtc_url, timeout=httpx.Timeout(10, read=None))
    app.state.nats_http = httpx.AsyncClient(base_url=settings.nats_monitor_url, timeout=3)
    log.info("gateway ready")
    yield
    await app.state.nats_http.aclose()
    await app.state.http.aclose()
    await app.state.bus.close()
    await app.state.db.close()


app = FastAPI(title="PrintFarm Ops Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
limiter = RateLimiter({"api": settings.rate_api_per_min, "commands": settings.rate_commands_per_min,
                       "uploads": settings.rate_uploads_per_min})
app.add_middleware(Guard, limiter=limiter, max_body=settings.max_body_bytes,
                   large_body_paths={"/api/v1/catalog/uploads": MAX_UPLOAD_BYTES + 64 * 1024})


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


@app.exception_handler(BodyTooLarge)
async def _too_large(request: Request, _exc: BodyTooLarge):
    return JSONResponse({"detail": "request body too large", "request_id": _rid(request)}, status_code=413)


@app.exception_handler(OperationalError)
@app.exception_handler(PoolTimeout)
async def _db_down(request: Request, exc: Exception):
    log.error("database unavailable on %s %s rid=%s: %s", request.method, request.url.path, _rid(request), exc)
    return JSONResponse({"detail": "database unavailable; try again shortly", "request_id": _rid(request)},
                        status_code=503, headers={"Retry-After": "5"})


@app.exception_handler(ConnectionClosedError)
@app.exception_handler(NoServersError)
async def _nats_down(request: Request, exc: Exception):
    log.error("NATS unavailable on %s %s rid=%s: %s", request.method, request.url.path, _rid(request), exc)
    return JSONResponse({"detail": "message bus unavailable; try again shortly", "request_id": _rid(request)},
                        status_code=503, headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def _unexpected(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s rid=%s", request.method, request.url.path, _rid(request))
    return JSONResponse({"detail": "internal error", "request_id": _rid(request)}, status_code=500)

for module in (auth, live, system, printers, cameras, approvals, orders, inventory, finance, catalog):
    app.include_router(module.router)


@app.get("/health")
async def health(request: Request):
    checks: dict = {"nats": request.app.state.bus.nc.is_connected}
    try:
        await request.app.state.db.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception:  # noqa: BLE001
        checks["postgres"] = False
    checks["migrations"] = await _migrations(request.app.state.db) if checks["postgres"] else None
    try:
        checks["go2rtc"] = (await request.app.state.http.get("/api", timeout=3)).status_code == 200
    except httpx.HTTPError:
        checks["go2rtc"] = False
    # go2rtc down = cameras degraded, not gateway down. An unmigrated or edited schema is down: queries would fail.
    core_ok = bool(checks["nats"] and checks["postgres"] and checks["migrations"] and checks["migrations"]["ok"])
    return JSONResponse({"ok": core_ok, **checks}, status_code=200 if core_ok else 503)


async def _migrations(db: Db) -> dict:
    """The schema against the migrations this build ships (the migrate service applies them before we start)."""
    try:
        rows = await db.fetchall("SELECT name, checksum FROM public.schema_migrations")
    except Exception:  # noqa: BLE001 - no table yet
        rows = []
    p = schema.plan(schema.bundled(), {r["name"]: r["checksum"] for r in rows})
    return {"ok": p.ok and not p.pending, **p.summary()}


# ---------------------------------------------------------------- commands
CATALOG_LINES = {"orders.create", "catalog.batch"}    # their catalog lines take filament and price from the catalog

async def _dispatch(request: Request, name: str, body: dict, claims: dict) -> dict:
    try:
        command_id = command_id_from(request.headers.get("idempotency-key"))
        env = build_command(name, body, claims["sub"], command_id)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    subject, db = env.nats_subject(), request.app.state.db
    await db.execute(
        "INSERT INTO gateway.audit_log (command_id, actor, subject, payload) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (command_id) DO NOTHING", (env.id, claims["sub"], subject, Jsonb(env.data)))
    try:
        ack = await request.app.state.bus.js.publish(subject, env.to_bytes(), headers={"Nats-Msg-Id": env.id},
                                                     timeout=settings.command_timeout_s)
    except (NoRespondersError, NoStreamResponseError) as exc:
        await _audit_outcome(db, env.id, "failed: no stream")
        raise HTTPException(503, f"no JetStream stream accepts {subject}") from exc
    except NatsTimeout as exc:
        await _audit_outcome(db, env.id, "failed: timeout")
        raise HTTPException(504, f"JetStream did not acknowledge {subject} within {settings.command_timeout_s} s") from exc
    await _audit_outcome(db, env.id, "duplicate" if ack.duplicate else "published")
    return {"accepted": True, "command_id": env.id, "subject": subject, "stream": ack.stream, "seq": ack.seq,
            "duplicate": bool(ack.duplicate)}


async def _audit_outcome(db: Db, command_id: str, outcome: str) -> None:
    await db.execute("UPDATE gateway.audit_log SET outcome = %s WHERE command_id = %s", (outcome, command_id))


async def _json_object(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


@app.get("/api/v1/commands")
async def command_catalog(_c: dict = Depends(auth.require_auth)):
    return {name: {"subject": spec.model.subject, "schema": spec.model.model_json_schema()} for name, spec in COMMANDS.items()}


@app.post("/api/v1/commands/{name}", status_code=202)
async def command(name: str, request: Request, claims: dict = Depends(auth.require_csrf)):
    if name not in COMMANDS:
        raise HTTPException(404, f"unknown command {name}")
    body = await _json_object(request)
    if name in CATALOG_LINES:
        body = await catalog.resolve_lines(request.app.state.db, body)
    return await _dispatch(request, name, body, claims)


@app.post("/api/v1/commands/printers/{printer_id}/{action}", status_code=202)
async def printer_command(printer_id: str, action: str, request: Request, claims: dict = Depends(auth.require_csrf)):
    if action not in PRINTER_ACTIONS or not PRINTER_ID.match(printer_id):
        raise HTTPException(400, "bad printer command")
    body = {**await _json_object(request), "printer_id": printer_id}
    if action == "pause":
        body.setdefault("reason", "paused from the dashboard")
    return await _dispatch(request, PRINTER_ACTIONS[action], body, claims)
