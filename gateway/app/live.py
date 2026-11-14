"""The dashboard's live feed: ``/ws``.

Every forwarded message comes from a JetStream stream and carries its stream name and sequence, so a client that
reconnects resumes exactly where it stopped instead of missing events. Protocol (JSON text frames):

client -> server
  {"op": "sub",    "topics": ["evt.>", "tlm.*.state"], "resume": {"EVENTS": 1234}}   live (+ catch-up from cursors)
  {"op": "unsub",  "topics": ["tlm.*.state"]}
  {"op": "replay", "id": "r1", "topics": ["evt.printer.>"], "since": "2026-11-14T09:00:00Z", "limit": 500}
server -> client
  {"type": "subscribed", "topics": [...], "refused": [...], "resumed": {"EVENTS": {"from": n, "gap": g, "truncated": b}}}
  {"type": "event",  "topic", "stream", "seq", "data": <envelope>, "ts"}
  {"type": "replay", "id", "topic", "stream", "seq", "data", "ts"}   ... then {"type": "replay_end", "id", "count", "truncated"}
  {"type": "hb", "ts"}   every 15 s without traffic, so the client can tell a quiet feed from a dead one

Only ``evt.``, ``tlm.`` and ``dlq.`` topics are accepted (commands and queries are never forwarded). Close codes:
4401 = not logged in (refresh, then reconnect), 4403 = page not served from an allowed origin.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import auth
from .commands import WS_MAX_TOPICS, ws_topic_allowed
from .config import settings
from .jsread import as_item, read_range, start_config, stream_for

router = APIRouter()
RESUME_MAX = 2000    # a client further behind than this is told to resync over REST
REPLAY_MAX = 2000
HEARTBEAT_S = 15
QUEUE_MAX = 5000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@router.websocket("/ws")
async def ws(socket: WebSocket):
    origin = socket.headers.get("origin")
    if origin and origin.rstrip("/") not in settings.allowed_origins():
        await socket.accept()
        await socket.close(code=4403)    # cross-site page: never stream to it
        return
    if not auth.ws_claims(socket):
        await socket.accept()            # accept first so the browser receives the 4401 code
        await socket.close(code=4401)    # client reacts: POST /auth/refresh, then reconnect
        return
    await socket.accept()
    state = socket.app.state
    js = state.bus.js
    state.ws_clients = getattr(state, "ws_clients", 0) + 1
    subs: dict = {}
    out: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)

    def put(item: dict) -> None:
        if out.full():
            out.get_nowait()  # drop the oldest; the client can resume from its cursor
        out.put_nowait(item)

    async def on_msg(msg) -> None:
        put({"type": "event", **as_item(msg)})

    async def sender() -> None:
        while True:
            try:
                item = await asyncio.wait_for(out.get(), HEARTBEAT_S)
            except TimeoutError:
                item = {"type": "hb"}
            await socket.send_json({**item, "ts": _now()})

    async def subscribe(topics: list, resume: dict) -> None:
        accepted, refused, resumed = [], [], {}
        for topic in topics[:WS_MAX_TOPICS]:
            if not ws_topic_allowed(topic) or topic in subs or len(subs) >= WS_MAX_TOPICS:
                refused.append(topic)
                continue
            stream, start = stream_for(topic), None
            cursor = resume.get(stream) if isinstance(resume, dict) else None
            if isinstance(cursor, int) and cursor > 0:
                st = (await js.stream_info(stream)).state
                if cursor > st.last_seq:  # the stream was reset since: the cursor means nothing any more
                    resumed[stream] = {"reset": True}
                else:
                    start = max(cursor + 1, st.last_seq - RESUME_MAX + 1, st.first_seq, 1)
                    resumed[stream] = {"from": start, "gap": st.last_seq - cursor,
                                       "truncated": st.last_seq - cursor > RESUME_MAX}
            subs[topic] = await js.subscribe(topic, cb=on_msg, ordered_consumer=True, config=start_config(seq=start))
            accepted.append(topic)
        put({"type": "subscribed", "topics": accepted, "refused": refused, "resumed": resumed})

    async def unsubscribe(topics: list) -> None:
        for topic in topics:
            if sub := subs.pop(topic, None):
                with contextlib.suppress(Exception):
                    await sub.unsubscribe()

    async def replay(req: dict) -> None:
        rid = str(req.get("id", ""))[:40]
        since = _parse_time(req.get("since"))
        topics = [t for t in req.get("topics", [])[:WS_MAX_TOPICS] if ws_topic_allowed(t)]
        limit = max(1, min(int(req.get("limit", 500)), REPLAY_MAX))
        if since is None or not topics:
            put({"type": "replay_end", "id": rid, "count": 0, "truncated": False, "error": "need topics and since"})
            return
        items = []
        for topic in topics:
            items.extend(as_item(m) for m in await read_range(js, topic, since=since, limit=limit))
        items.sort(key=lambda i: (str(i["data"].get("time", "")) if isinstance(i["data"], dict) else "", i["seq"]))
        truncated = len(items) > limit
        for item in items[-limit:]:
            put({"type": "replay", "id": rid, **item})
        put({"type": "replay_end", "id": rid, "count": min(len(items), limit), "truncated": truncated})

    task = asyncio.create_task(sender())
    try:
        while True:
            msg = await socket.receive_json()
            op = msg.get("op") if isinstance(msg, dict) else None
            if op == "sub":
                await subscribe(list(msg.get("topics", [])), msg.get("resume") or {})
            elif op == "unsub":
                await unsubscribe(list(msg.get("topics", [])))
            elif op == "replay":
                await replay(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
        await unsubscribe(list(subs))
        state.ws_clients -= 1
