"""Request guards for every HTTP call: a request id, per-client rate limits and a body size limit.

Plain ASGI (not BaseHTTPMiddleware) so streamed responses (MJPEG camera feeds) pass through unbuffered.

- Request id: ``X-Request-ID`` from the client (if sane) or a new one; echoed on the response and put on log lines
  and 500 bodies, so an error the operator sees can be found in ``docker compose logs gateway``.
- Rate limits, per client IP (uvicorn takes it from Caddy's X-Forwarded-For): a sliding one-minute window per
  bucket. ``commands`` = POST /api/v1/commands*, ``uploads`` = POST /api/v1/catalog/uploads, ``api`` = everything
  else under /api. Over the limit: 429 with Retry-After. /health and the WebSocket are not limited (login has its
  own lockout in auth.py).
- Body size: Content-Length over the limit is refused at once (413); a chunked body is counted as it arrives.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import deque

log = logging.getLogger("pf.gateway")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
WINDOW_S = 60.0
MAX_CLIENTS = 10_000          # tracked (ip, bucket) pairs; the oldest are forgotten beyond this


class RateLimiter:
    def __init__(self, limits: dict[str, int]):
        self.limits = limits
        self.hits: dict[tuple[str, str], deque[float]] = {}

    def reset(self) -> None:
        self.hits.clear()

    def check(self, client: str, bucket: str, now: float | None = None) -> float:
        """0 if allowed (and counted), else the seconds until the next request would be allowed."""
        limit = self.limits.get(bucket, 0)
        if limit <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        q = self.hits.get((client, bucket))
        if q is None:
            if len(self.hits) >= MAX_CLIENTS:
                self.hits.pop(next(iter(self.hits)))
            q = self.hits[(client, bucket)] = deque()
        while q and now - q[0] >= WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return max(0.1, WINDOW_S - (now - q[0]))
        q.append(now)
        return 0.0


def bucket_for(method: str, path: str) -> str | None:
    if not path.startswith("/api/"):
        return None
    if method == "POST" and path.startswith("/api/v1/catalog/uploads"):
        return "uploads"
    if method == "POST" and path.startswith("/api/v1/commands"):
        return "commands"
    return "api"


class BodyTooLarge(Exception):
    pass


class Guard:
    def __init__(self, app, limiter: RateLimiter, max_body: int, large_body_paths: dict[str, int]):
        self.app, self.limiter, self.max_body, self.large = app, limiter, max_body, large_body_paths

    def _limit_for(self, path: str) -> int:
        return next((n for prefix, n in self.large.items() if path.startswith(prefix)), self.max_body)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        rid = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = rid if REQUEST_ID.match(rid) else uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        path, method = scope["path"], scope["method"]

        async def reply(status: int, detail: str, extra: list[tuple[bytes, bytes]] = ()):
            body = json.dumps({"detail": detail, "request_id": rid}).encode()
            await send({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                (b"x-request-id", rid.encode()), *extra]})
            await send({"type": "http.response.body", "body": body})

        bucket = bucket_for(method, path)
        if bucket:
            client = (scope.get("client") or ("?", 0))[0]
            wait = self.limiter.check(client, bucket)
            if wait:
                log.warning("rate limit %s %s %s %s rid=%s", client, bucket, method, path, rid)
                return await reply(429, f"too many requests; retry in {int(wait) + 1} s",
                                   [(b"retry-after", str(int(wait) + 1).encode())])

        limit = self._limit_for(path)
        length = headers.get(b"content-length")
        if length is not None:
            try:
                too_big = int(length) > limit
            except ValueError:
                return await reply(400, "bad Content-Length")
            if too_big:
                return await reply(413, f"request body over {limit} bytes")

        seen = 0
        started = False

        async def counted_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise BodyTooLarge
            return msg

        async def tagged_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
                msg = {**msg, "headers": [*msg.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(msg)

        try:
            await self.app(scope, counted_receive, tagged_send)
        except BodyTooLarge:
            if not started:
                await reply(413, f"request body over {limit} bytes")
