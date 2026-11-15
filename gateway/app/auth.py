"""Single-admin auth: JWT access cookie + rotating refresh cookie + CSRF double-submit."""
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_ph = PasswordHasher()
_failures: dict[str, list[float]] = {}  # ip -> failure timestamps (single process is enough here)

ACCESS, REFRESH, CSRF = "pf_access", "pf_refresh", "pf_csrf"
log = logging.getLogger("pf.gateway.auth")


async def _audit(request: Request, subject: str, actor: str, outcome: str, **detail) -> None:
    """Sign-ins, failures, lockouts and sign-outs go to the same audit log as commands (System screen).
    Best effort: a database hiccup must not turn a sign-in into a 500, so it is logged instead."""
    ip = request.client.host if request.client else "?"
    try:
        await request.app.state.db.execute(
            "INSERT INTO gateway.audit_log (command_id, actor, subject, payload, outcome) VALUES (%s, %s, %s, %s, %s)",
            (f"auth-{uuid.uuid4().hex}", actor[:200], subject, Jsonb({"ip": ip, **detail}), outcome))
    except Exception:  # noqa: BLE001
        log.exception("audit of %s for %s failed", subject, ip)


def _same(a: str, b: str) -> bool:
    """Constant-time compare that also accepts non-ASCII input (hmac.compare_digest raises on non-ASCII str)."""
    return hmac.compare_digest(a.encode(), b.encode())


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_access() -> str:
    now = int(time.time())
    return jwt.encode({"sub": settings.admin_username, "iat": now, "exp": now + settings.access_ttl_s,
                       "jti": uuid.uuid4().hex}, settings.jwt_secret, algorithm="HS256")


def _refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=settings.refresh_ttl_s)


def _set_cookies(resp: Response, access: str, refresh: str) -> None:
    common = {"secure": settings.cookie_secure, "samesite": "strict"}
    resp.set_cookie(ACCESS, access, max_age=settings.access_ttl_s, httponly=True, path="/", **common)
    resp.set_cookie(REFRESH, refresh, max_age=settings.refresh_ttl_s, httponly=True, path="/api/v1/auth", **common)
    resp.set_cookie(CSRF, secrets.token_urlsafe(24), max_age=settings.refresh_ttl_s, httponly=False, path="/", **common)


def _decode(token: str | None) -> dict:
    if not token:
        raise HTTPException(401, "not authenticated")
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token") from None


def require_auth(request: Request) -> dict:
    return _decode(request.cookies.get(ACCESS))


def require_csrf(request: Request, _claims: dict = Depends(require_auth)) -> dict:
    cookie, header = request.cookies.get(CSRF), request.headers.get("x-csrf-token")
    if not cookie or not header or not _same(cookie, header):
        raise HTTPException(403, "CSRF check failed")
    return _claims


def ws_claims(ws: WebSocket) -> dict | None:
    try:
        return _decode(ws.cookies.get(ACCESS))
    except HTTPException:
        return None


class LoginIn(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1000)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < settings.login_lockout_s]
    if len(recent) >= settings.login_max_failures:
        await _audit(request, "auth.login", body.username, "locked out")
        raise HTTPException(429, f"locked out; retry in {int(settings.login_lockout_s - (now - recent[0]))} s")
    ok = _same(body.username, settings.admin_username)
    try:
        _ph.verify(settings.admin_password_hash, body.password)
    except (VerificationError, InvalidHashError):
        ok = False
    if not ok:
        recent.append(now)
        _failures[ip] = recent
        await _audit(request, "auth.login", body.username, "failed", attempt=len(recent))
        if len(recent) >= settings.login_max_failures:
            log.warning("login locked out for %s after %d failures", ip, len(recent))
        raise HTTPException(401, f"wrong username or password; {settings.login_max_failures - len(recent)} attempts left")
    _failures.pop(ip, None)
    refresh = secrets.token_urlsafe(32)
    await request.app.state.db.execute(
        "INSERT INTO gateway.refresh_tokens (token_hash, expires_at) VALUES (%s, %s)", (_hash_token(refresh), _refresh_expiry()))
    _set_cookies(response, _issue_access(), refresh)
    await _audit(request, "auth.login", settings.admin_username, "done")
    return {"user": settings.admin_username}


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    old = request.cookies.get(REFRESH)
    if not old:
        raise HTTPException(401, "no refresh token")
    row = await request.app.state.db.fetchrow(
        "UPDATE gateway.refresh_tokens SET revoked_at = now() "
        "WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now() RETURNING id", (_hash_token(old),))
    if not row:
        await _audit(request, "auth.refresh", "?", "refused")   # an old (rotated) token reused: worth seeing
        raise HTTPException(401, "refresh token invalid")
    new = secrets.token_urlsafe(32)
    await request.app.state.db.execute(
        "INSERT INTO gateway.refresh_tokens (token_hash, expires_at) VALUES (%s, %s)", (_hash_token(new), _refresh_expiry()))
    _set_cookies(response, _issue_access(), new)
    return {"user": settings.admin_username}


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(REFRESH)
    if token:
        await request.app.state.db.execute(
            "UPDATE gateway.refresh_tokens SET revoked_at = now() WHERE token_hash = %s", (_hash_token(token),))
    for name, path in ((ACCESS, "/"), (REFRESH, "/api/v1/auth"), (CSRF, "/")):
        response.delete_cookie(name, path=path)
    if token:
        await _audit(request, "auth.logout", settings.admin_username, "done")
    return {"ok": True}


@router.get("/me")
async def me(claims: dict = Depends(require_auth)):
    return {"user": claims["sub"], "expires_at": claims["exp"]}
