"""PrintFarm Ops FastAPI gateway (BFF): REST, commands, camera proxy, WebSocket bridge."""
import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import asyncpg
import httpx
import nats
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from nats.errors import NoRespondersError, TimeoutError as NatsTimeout

from pf_common.nats_setup import ensure_streams
from pf_common.subjects import COMMANDS, PRINTER_CMD_PREFIX, WS_ALLOWED_PREFIXES

from . import auth
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pf.gateway")
CAMERA_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    app.state.nc = await nats.connect(settings.nats_url, name="gateway", max_reconnect_attempts=-1)
    await ensure_streams(app.state.nc.jetstream())
    app.state.http = httpx.AsyncClient(base_url=settings.go2rtc_url, timeout=httpx.Timeout(10, read=None))
    log.info("gateway ready")
    yield
    await app.state.http.aclose()
    await app.state.nc.drain()
    await app.state.db.close()


app = FastAPI(title="PrintFarm Ops Gateway", lifespan=lifespan)
app.include_router(auth.router)


@app.get("/health")
async def health(request: Request):
    checks = {"nats": request.app.state.nc.is_connected}
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
async def _send_command(request: Request, subject: str, payload: dict, claims: dict) -> dict:
    command_id = request.headers.get("idempotency-key") or uuid.uuid4().hex
    body = {"command_id": command_id, "actor": claims["sub"], "ts": datetime.now(timezone.utc).isoformat(), "data": payload}
    await request.app.state.db.execute(
        "INSERT INTO audit_log (command_id, actor, subject, payload) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        command_id, claims["sub"], subject, json.dumps(payload))
    try:
        reply = await request.app.state.nc.request(subject, json.dumps(body).encode(), timeout=settings.command_timeout_s)
    except NoRespondersError:
        raise HTTPException(503, f"no service is handling {subject}")
    except NatsTimeout:
        raise HTTPException(504, f"{subject} did not answer within {settings.command_timeout_s} s")
    try:
        result = json.loads(reply.data or b"{}")
    except json.JSONDecodeError:
        result = {"raw": reply.data.decode(errors="replace")}
    return {"accepted": True, "command_id": command_id, "subject": subject, "reply": result}


@app.post("/api/v1/commands/{name}", status_code=202)
async def command(name: str, request: Request, claims: dict = Depends(auth.require_csrf)):
    subject = COMMANDS.get(name)
    if not subject:
        raise HTTPException(404, f"unknown command {name}")
    return await _send_command(request, subject, await request.json(), claims)


@app.post("/api/v1/commands/printers/{printer_id}/{action}", status_code=202)
async def printer_command(printer_id: str, action: str, request: Request, claims: dict = Depends(auth.require_csrf)):
    if action not in ("pause", "resume", "cancel") or not CAMERA_ID.match(printer_id):
        raise HTTPException(400, "bad printer command")
    return await _send_command(request, f"{PRINTER_CMD_PREFIX}.{printer_id}", {"action": action}, claims)


# ---------------------------------------------------------------- cameras (go2rtc)
@app.get("/api/v1/cameras/{cam_id}/mjpeg")
async def camera_mjpeg(cam_id: str, request: Request, _c: dict = Depends(auth.require_auth)):
    if not CAMERA_ID.match(cam_id):
        raise HTTPException(400, "bad camera id")
    client: httpx.AsyncClient = request.app.state.http
    upstream = await client.send(client.build_request("GET", "/api/stream.mjpeg", params={"src": cam_id}), stream=True)
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
    r = await request.app.state.http.get("/api/frame.jpeg", params={"src": cam_id}, timeout=10)
    if r.status_code != 200:
        raise HTTPException(502, f"no frame from {cam_id}")
    return StreamingResponse(iter([r.content]), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/v1/cameras")
async def cameras(request: Request, _c: dict = Depends(auth.require_auth)):
    r = await request.app.state.http.get("/api/streams", timeout=5)
    return {"cameras": sorted(r.json().keys()) if r.status_code == 200 else []}


# ---------------------------------------------------------------- finance (read side)
@app.get("/api/v1/finance/summary")
async def finance_summary(request: Request, month: str | None = None, _c: dict = Depends(auth.require_auth)):
    first = date.fromisoformat(f"{month}-01") if month else date.today().replace(day=1)
    q = """
      WITH bounds AS (SELECT $1::date AS a, ($1::date + interval '1 month')::date AS b)
      SELECT
        (SELECT coalesce(sum(net_agorot), 0) FROM income_entries, bounds WHERE entry_date >= a AND entry_date < b) AS revenue_net,
        (SELECT coalesce(sum(net_agorot), 0) FROM expense_entries, bounds
           WHERE entry_date >= a AND entry_date < b AND kind IN ('cash', 'estimated')) AS expenses_net,
        (SELECT coalesce(sum(net_agorot), 0) FROM expense_entries, bounds
           WHERE entry_date >= a AND entry_date < b AND kind = 'allocated') AS allocated_cost_net
    """
    row = await request.app.state.db.fetchrow(q, first)
    rev, exp = row["revenue_net"], row["expenses_net"]
    return {"month": first.isoformat()[:7], "revenue_net_agorot": rev, "expenses_net_agorot": exp,
            "profit_net_agorot": rev - exp, "margin_pct": round((rev - exp) / rev * 100, 1) if rev else None,
            "allocated_cost_net_agorot": row["allocated_cost_net"],
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
    nc = socket.app.state.nc
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
                                    "ts": datetime.now(timezone.utc).isoformat(), "data": data})

    sender = asyncio.create_task(pump())
    try:
        while True:
            msg = await socket.receive_json()
            if msg.get("op") == "sub":
                accepted = []
                for topic in msg.get("topics", [])[:20]:
                    plain = topic.rstrip(">*").rstrip(".") + "."
                    # the topic must sit inside an allowed prefix, so "printer.>" (which would include commands) is refused
                    if any(plain.startswith(p) for p in WS_ALLOWED_PREFIXES):
                        subs.append(await nc.subscribe(topic, cb=on_msg))
                        accepted.append(topic)
                await socket.send_json({"type": "subscribed", "topics": accepted})
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        for s in subs:
            await s.unsubscribe()
