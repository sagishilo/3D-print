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
from datetime import date

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from nats.errors import NoRespondersError
from nats.errors import TimeoutError as NatsTimeout
from nats.js.errors import NoStreamResponseError
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from printfarm.bus.nats_bus import NatsBus

from . import approvals, auth, cameras, live, orders, printers, system
from .commands import COMMANDS, PRINTER_ACTIONS, PRINTER_ID, build_command, command_id_from
from .config import settings
from .db import Db

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


app = FastAPI(title="PrintFarm Ops Gateway", lifespan=lifespan)
for module in (auth, live, system, printers, cameras, approvals, orders):
    app.include_router(module.router)


@app.get("/health")
async def health(request: Request):
    checks = {"nats": request.app.state.bus.nc.is_connected}
    try:
        await request.app.state.db.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception:  # noqa: BLE001
        checks["postgres"] = False
    try:
        checks["go2rtc"] = (await request.app.state.http.get("/api", timeout=3)).status_code == 200
    except httpx.HTTPError:
        checks["go2rtc"] = False
    core_ok = checks["nats"] and checks["postgres"]  # go2rtc down = cameras degraded, not gateway down
    return JSONResponse({"ok": core_ok, **checks}, status_code=200 if core_ok else 503)


# ---------------------------------------------------------------- commands
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
    return await _dispatch(request, name, await _json_object(request), claims)


@app.post("/api/v1/commands/printers/{printer_id}/{action}", status_code=202)
async def printer_command(printer_id: str, action: str, request: Request, claims: dict = Depends(auth.require_csrf)):
    if action not in PRINTER_ACTIONS or not PRINTER_ID.match(printer_id):
        raise HTTPException(400, "bad printer command")
    body = {**await _json_object(request), "printer_id": printer_id}
    if action == "pause":
        body.setdefault("reason", "paused from the dashboard")
    return await _dispatch(request, PRINTER_ACTIONS[action], body, claims)


# ---------------------------------------------------------------- finance (read side)
FINANCE_SUMMARY_SQL = """
  WITH bounds AS (SELECT %(first)s::date AS a, (%(first)s::date + interval '1 month')::date AS b)
  SELECT
    (SELECT coalesce(sum(net_agorot), 0) FROM finance.income_entries, bounds
       WHERE entry_date >= a AND entry_date < b) AS revenue_net,
    (SELECT coalesce(sum(net_agorot), 0) FROM finance.expense_entries, bounds
       WHERE entry_date >= a AND entry_date < b AND kind IN ('cash', 'estimated')) AS expenses_net,
    (SELECT coalesce(sum(net_agorot), 0) FROM finance.expense_entries, bounds
       WHERE entry_date >= a AND entry_date < b AND kind = 'allocated') AS allocated_cost_net
"""


@app.get("/api/v1/finance/summary")
async def finance_summary(request: Request, month: str | None = None, _c: dict = Depends(auth.require_auth)):
    try:
        first = date.fromisoformat(f"{month}-01") if month else date.today().replace(day=1)
    except ValueError:
        raise HTTPException(400, "month must look like 2026-11") from None
    row = await request.app.state.db.fetchrow(FINANCE_SUMMARY_SQL, {"first": first})
    rev, exp = int(row["revenue_net"]), int(row["expenses_net"])
    return {"month": first.isoformat()[:7], "revenue_net_agorot": rev, "expenses_net_agorot": exp,
            "profit_net_agorot": rev - exp, "margin_pct": round((rev - exp) / rev * 100, 1) if rev else None,
            "allocated_cost_net_agorot": int(row["allocated_cost_net"]),
            "note": "allocated = per-job filament/electricity; used for product margins, excluded from profit"}
