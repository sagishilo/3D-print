"""Sign-in for the dashboard's users: JWT access cookie + rotating refresh cookie + CSRF double-submit, and roles.

Roles (gateway.users, migration 012): viewer < operator < owner. ``require_auth`` checks the session against the
user's current row (cached a few seconds), so disabling a user, changing a role or resetting a password ends their
sessions at once, not when the 15-minute access token expires.
"""
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("timing-equaliser-not-a-password")    # verified when the username is unknown
_failures: dict[str, list[float]] = {}         # "ip:<ip>" / "user:<name>" -> failure timestamps (single process)
_users: dict[int, tuple[float, dict | None]] = {}   # uid -> (fetched at, row); see current_user

ACCESS, REFRESH, CSRF = "pf_access", "pf_refresh", "pf_csrf"
ROLE_RANK = {"viewer": 0, "operator": 1, "owner": 2}
MIN_PASSWORD = 12
PASSWORD_CHANGE_PATHS = ("/api/v1/auth/",)      # all a user with a temporary password may call
log = logging.getLogger("pf.gateway.auth")


def allows(role: str | None, min_role: str) -> bool:
    return ROLE_RANK.get(role or "", -1) >= ROLE_RANK[min_role]


def hash_password(password: str) -> str:
    return _ph.hash(password)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"the password needs at least {MIN_PASSWORD} characters"
    if password.strip() != password or len(set(password)) < 4:
        return "choose a less predictable password"
    return None


def temporary_password() -> str:
    """A one-time password an owner hands over; the user must replace it at the first sign-in."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/l/I
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


async def _audit(request: Request, subject: str, actor: str, outcome: str, **detail) -> None:
    """Sign-ins, failures, lockouts and sign-outs go to the same audit log as commands (System > Audit).
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


def _issue_access(user: dict) -> str:
    now = int(time.time())
    return jwt.encode({"sub": user["username"], "uid": user["id"], "role": user["role"], "sv": user["session_version"],
                       "iat": now, "exp": now + settings.access_ttl_s, "jti": uuid.uuid4().hex},
                      settings.jwt_secret, algorithm="HS256")


def _refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=settings.refresh_ttl_s)


def _set_cookies(resp: Response, access: str, refresh: str) -> None:
    common = {"secure": settings.cookie_secure, "samesite": "strict"}
    resp.set_cookie(ACCESS, access, max_age=settings.access_ttl_s, httponly=True, path="/", **common)
    resp.set_cookie(REFRESH, refresh, max_age=settings.refresh_ttl_s, httponly=True, path="/api/v1/auth", **common)
    resp.set_cookie(CSRF, secrets.token_urlsafe(24), max_age=settings.refresh_ttl_s, httponly=False, path="/", **common)


async def _start_session(request: Request, response: Response, user: dict) -> None:
    refresh = secrets.token_urlsafe(32)
    await request.app.state.db.execute(
        "INSERT INTO gateway.refresh_tokens (token_hash, expires_at, user_id) VALUES (%s, %s, %s)",
        (_hash_token(refresh), _refresh_expiry(), user["id"]))
    _set_cookies(response, _issue_access(user), refresh)


def _decode(token: str | None) -> dict:
    if not token:
        raise HTTPException(401, "not authenticated")
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token") from None


def forget(uid: int) -> None:
    """Drop a cached user row (called whenever the row changes)."""
    _users.pop(uid, None)


async def current_user(request: Request, uid: int) -> dict | None:
    hit = _users.get(uid)
    if hit and time.monotonic() - hit[0] < settings.user_cache_s:
        return hit[1]
    row = await request.app.state.users.get(uid)
    _users[uid] = (time.monotonic(), row)
    return row


async def require_auth(request: Request) -> dict:
    """The signed-in user's claims, checked against their current row: role, display name, and whether a
    password change is still due (then only /api/v1/auth/* is allowed)."""
    claims = _decode(request.cookies.get(ACCESS))
    uid = claims.get("uid")
    user = await current_user(request, uid) if isinstance(uid, int) else None
    if not user or user["disabled"] or user["session_version"] != claims.get("sv"):
        raise HTTPException(401, "session ended; sign in again")
    if user["must_change_password"] and not request.url.path.startswith(PASSWORD_CHANGE_PATHS):
        raise HTTPException(403, "password change required")
    return {**claims, "sub": user["username"], "role": user["role"], "name": user["display_name"] or user["username"]}


async def require_csrf(request: Request, claims: dict = Depends(require_auth)) -> dict:
    cookie, header = request.cookies.get(CSRF), request.headers.get("x-csrf-token")
    if not cookie or not header or not _same(cookie, header):
        raise HTTPException(403, "CSRF check failed")
    return claims


def _check_role(claims: dict, min_role: str) -> dict:
    if not allows(claims.get("role"), min_role):
        raise HTTPException(403, f"your role ({claims.get('role')}) cannot do this: it needs {min_role}")
    return claims


def require_role(min_role: str):
    """A read dependency: signed in, with at least ``min_role``."""
    async def dep(claims: dict = Depends(require_auth)) -> dict:
        return _check_role(claims, min_role)
    return dep


def require_csrf_role(min_role: str):
    """A change dependency: signed in, CSRF token, and at least ``min_role``."""
    async def dep(claims: dict = Depends(require_csrf)) -> dict:
        return _check_role(claims, min_role)
    return dep


# The dependencies routes use (module-level, so FastAPI sees one object per rule).
OPERATOR = require_role("operator")
OWNER = require_role("owner")
OPERATOR_CHANGE = require_csrf_role("operator")
OWNER_CHANGE = require_csrf_role("owner")


def ws_claims(ws: WebSocket) -> dict | None:
    try:
        return _decode(ws.cookies.get(ACCESS))
    except HTTPException:
        return None


# ---------------------------------------------------------------- sign in / out
class LoginIn(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1000)


def _recent(key: str, now: float) -> list[float]:
    return [t for t in _failures.get(key, []) if now - t < settings.login_lockout_s]


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    name = body.username.strip()
    keys = (f"ip:{ip}", f"user:{name.lower()}")
    now = time.time()
    for key in keys:
        recent = _recent(key, now)
        if len(recent) >= settings.login_max_failures:
            await _audit(request, "auth.login", name, "locked out", by="ip" if key.startswith("ip:") else "username")
            raise HTTPException(429, f"locked out; retry in {int(settings.login_lockout_s - (now - recent[0]))} s")
    user = await request.app.state.users.by_username(name) if name else None
    try:
        _ph.verify(user["password_hash"] if user else _DUMMY_HASH, body.password)
        ok = user is not None and not user["disabled"]
    except (VerificationError, InvalidHashError):
        ok = False
    if not ok:
        counts = []
        for key in keys:
            recent = _recent(key, now) + [now]
            _failures[key] = recent
            counts.append(len(recent))
        worst = max(counts)
        await _audit(request, "auth.login", name, "failed", attempt=worst, disabled=bool(user and user["disabled"]))
        if worst >= settings.login_max_failures:
            log.warning("login locked out for %s / %s after %d failures", ip, name, worst)
        raise HTTPException(401, f"wrong username or password; {max(settings.login_max_failures - worst, 0)} attempts left")
    for key in keys:
        _failures.pop(key, None)
    if _ph.check_needs_rehash(user["password_hash"]):
        user = await request.app.state.users.update(user["id"], password_hash=hash_password(body.password))
    user = await request.app.state.users.update(user["id"], last_login_at=datetime.now(UTC))
    forget(user["id"])
    await _start_session(request, response, user)
    await _audit(request, "auth.login", user["username"], "done", role=user["role"])
    return _me(user, int(time.time()) + settings.access_ttl_s)


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    old = request.cookies.get(REFRESH)
    if not old:
        raise HTTPException(401, "no refresh token")
    row = await request.app.state.db.fetchrow(
        "UPDATE gateway.refresh_tokens SET revoked_at = now() "
        "WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now() RETURNING id, user_id", (_hash_token(old),))
    if not row:
        await _audit(request, "auth.refresh", "?", "refused")   # an old (rotated) token reused: worth seeing
        raise HTTPException(401, "refresh token invalid")
    users = request.app.state.users
    # Sessions from before user accounts (migration 012) have no user: they were the .env admin's.
    user = await users.get(row["user_id"]) if row.get("user_id") else await users.by_username(settings.admin_username)
    if not user or user["disabled"]:
        raise HTTPException(401, "session ended; sign in again")
    await _start_session(request, response, user)
    return _me(user, int(time.time()) + settings.access_ttl_s)


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(REFRESH)
    if token:
        await request.app.state.db.execute(
            "UPDATE gateway.refresh_tokens SET revoked_at = now() WHERE token_hash = %s", (_hash_token(token),))
    for name, path in ((ACCESS, "/"), (REFRESH, "/api/v1/auth"), (CSRF, "/")):
        response.delete_cookie(name, path=path)
    try:
        who = _decode(request.cookies.get(ACCESS)).get("sub", "?")
    except HTTPException:
        who = "?"
    if token:
        await _audit(request, "auth.logout", who, "done")
    return {"ok": True}


def _me(user: dict, expires_at: int) -> dict:
    return {"user": user["username"], "uid": user["id"], "display_name": user["display_name"] or user["username"],
            "role": user["role"], "must_change_password": user["must_change_password"], "prefs": user.get("prefs") or {},
            "expires_at": expires_at}


@router.get("/me")
async def me(request: Request, claims: dict = Depends(require_auth)):
    user = await current_user(request, claims["uid"])
    return _me(user, claims["exp"])


class PrefsIn(BaseModel):
    """Per-person display preferences; only the fields sent change. Unknown keys are refused."""
    model_config = ConfigDict(extra="forbid")
    theme: Literal["system", "light", "dark"] | None = None
    density: Literal["comfortable", "compact"] | None = None
    mode: Literal["simple", "expert"] | None = None   # simple hides the technical panels


@router.patch("/prefs")
async def save_prefs(body: PrefsIn, request: Request, claims: dict = Depends(require_csrf)):
    """Theme, density and Simple / Expert mode follow the person to every device they sign in on."""
    users = request.app.state.users
    user = await users.get(claims["uid"])
    prefs = {**(user.get("prefs") or {}), **body.model_dump(exclude_none=True)}
    user = await users.update(user["id"], prefs=prefs)
    forget(user["id"])
    return _me(user, claims["exp"])


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=1000)
    new_password: str = Field(max_length=1000)


@router.post("/password")
async def change_password(body: PasswordIn, request: Request, response: Response, claims: dict = Depends(require_csrf)):
    """Change your own password (required after an owner set a temporary one). Ends your other sessions."""
    users = request.app.state.users
    user = await users.get(claims["uid"])
    try:
        _ph.verify(user["password_hash"], body.current_password)
    except (VerificationError, InvalidHashError):
        await _audit(request, "auth.password", user["username"], "failed")
        raise HTTPException(400, "the current password is wrong") from None
    if problem := password_problem(body.new_password):
        raise HTTPException(422, problem)
    if body.new_password == body.current_password:
        raise HTTPException(422, "choose a new password, not the current one")
    await users.revoke_sessions(user["id"])
    user = await users.update(user["id"], password_hash=hash_password(body.new_password), must_change_password=False,
                              session_version=user["session_version"] + 1, password_changed_at=datetime.now(UTC))
    forget(user["id"])
    await _start_session(request, response, user)
    await _audit(request, "auth.password", user["username"], "done")
    return _me(user, int(time.time()) + settings.access_ttl_s)
