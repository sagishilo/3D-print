"""PrintFarm Ops FastAPI gateway (BFF): REST, commands, camera proxy, WebSocket bridge.

Commands: ``POST /api/v1/commands/{name}`` validates the body against the command's contract (``commands.py``) and
publishes it to JetStream with ``Nats-Msg-Id`` = Idempotency-Key. The stream's acknowledgement means "durably
accepted"; the result arrives later as an event over ``/ws``. The gateway never waits for an agent to act.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from nats.errors import NoRespondersError
from nats.errors import TimeoutError as NatsTimeout
from nats.js.errors import NoStreamResponseError
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from printfarm.bus.nats_bus import NatsBus

from . import auth
from .commands import COMMANDS, PRINTER_ACTIONS, PRINTER_ID, WS_MAX_TOPICS, build_command, command_id_from, ws_topic_allowed
from .config import settings
from .db import Db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pf.gateway")
CAMERA_ID = PRINTER_ID  # go2rtc stream names are the printer ids


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = Db(settings.database_url)
    await app.state.db.open()
    app.state.bus = await NatsBus.connect(settings.nats_url, name="gateway")  # also ensures the shared JetStream topology
    app.state.http = httpx.AsyncClient(base_url=settings.go2rtc_url, timeout=httpx.Timeout(10, read=None))
    log.info("gateway ready")
    yield
    await app.state.http.aclose()
    await app.state.bus.close()
    await app.state.db.close()


app = FastAPI(title="PrintFarm Ops Gateway", lifespan=lifespan)
app.include_router(auth.router)


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


# ---------------------------------------------------------------- cameras (go2rtc)
def _relay_error(cam_id: str, exc: httpx.HTTPError) -> HTTPException:
    """A camera that sends nothing is a 504, an unreachable relay a 502 - never an unhandled 500."""
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(504, f"camera {cam_id} sent no frame in time")
    return HTTPException(502, f"camera relay unreachable ({type(exc).__name__})")


@app.get("/api/v1/cameras/{cam_id}/mjpeg")
async def camera_mjpeg(cam_id: str, request: Request, _c: dict = Depends(auth.require_auth)):
    if not CAMERA_ID.match(cam_id):
        raise HTTPException(400, "bad camera id")
    client: httpx.AsyncClient = request.app.state.http
    try:
        upstream = await client.send(client.build_request("GET", "/api/stream.mjpeg", params={"src": cam_id},
                                                          timeout=httpx.Timeout(10, read=None)), stream=True)
    except httpx.HTTPError as exc:
        raise _relay_error(cam_id, exc) from exc
    if upstream.status_code != 200:
        await upstream.aclose()
        raise HTTPException(502, f"go2rtc returned {upstream.status_code} for {cam_id}")

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                if await request.is_disconnected():  # tab closed: stop, so go2rtc stops reading the camera
                    break
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(body(), media_type=upstream.headers.get("content-type", "multipart/x-mixed-replace"),
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/v1/cameras/{cam_id}/snapshot.jpg")
async def camera_snapshot(cam_id: str, request: Request, _c: dict = Depends(auth.require_auth)):
    if not CAMERA_ID.match(cam_id):
        raise HTTPException(400, "bad camera id")
    try:
        r = await request.app.state.http.get("/api/frame.jpeg", params={"src": cam_id}, timeout=10)
    except httpx.HTTPError as exc:
        raise _relay_error(cam_id, exc) from exc
    if r.status_code != 200 or not r.content.startswith(b"\xff\xd8"):  # go2rtc can answer 200 with an empty body
        raise HTTPException(502, f"no frame from {cam_id}")
    return StreamingResponse(iter([r.content]), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/v1/cameras")
async def cameras(request: Request, _c: dict = Depends(auth.require_auth)):
    try:
        r = await request.app.state.http.get("/api/streams", timeout=5)
    except httpx.HTTPError as exc:
        raise _relay_error("list", exc) from exc
    return {"cameras": sorted(r.json().keys()) if r.status_code == 200 else []}


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


# ---------------------------------------------------------------- live events
@app.websocket("/ws")
async def ws(socket: WebSocket):
    claims = auth.ws_claims(socket)
    if not claims:
        await socket.accept()            # accept first so the browser receives the 4401 code
        await socket.close(code=4401)    # client reacts: POST /auth/refresh, then reconnect
        return
    await socket.accept()
    nc = socket.app.state.bus.nc
    subs, seq, queue = [], 0, asyncio.Queue(maxsize=1000)

    async def on_msg(msg):
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            data = {"raw": msg.data.decode(errors="replace")}
        if queue.full():
            queue.get_nowait()  # drop oldest; client resyncs via REST if it falls behind
        queue.put_nowait((msg.subject, data))

    async def pump():
        nonlocal seq
        while True:
            subject, data = await queue.get()
            seq += 1
            await socket.send_json({"topic": subject, "type": "event", "seq": seq,
                                    "ts": datetime.now(UTC).isoformat(), "data": data})

    sender = asyncio.create_task(pump())
    try:
        while True:
            msg = await socket.receive_json()
            if msg.get("op") == "sub":
                accepted, refused = [], []
                for topic in msg.get("topics", [])[:WS_MAX_TOPICS]:
                    if ws_topic_allowed(topic) and len(subs) < WS_MAX_TOPICS:
                        subs.append(await nc.subscribe(topic, cb=on_msg))
                        accepted.append(topic)
                    else:
                        refused.append(topic)
                await socket.send_json({"type": "subscribed", "topics": accepted, "refused": refused})
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        for s in subs:
            await s.unsubscribe()
