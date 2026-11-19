"""Users and roles (owner-only): list, add, change role or name, disable / enable, reset password.

New users and password resets get a one-time temporary password, shown once to the owner; the user must replace it
at the first sign-in. Safety rules: an owner cannot demote or disable themselves, and the last active owner cannot be
demoted or disabled (nobody could manage users any more). Every change is audited, never with a password.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from . import auth

COLUMNS = ("id, username, display_name, role, password_hash, must_change_password, disabled, session_version, prefs, "
           "created_at, created_by, updated_at, last_login_at, password_changed_at")
EDITABLE = {"username", "display_name", "role", "password_hash", "must_change_password", "disabled", "session_version",
            "prefs", "last_login_at", "password_changed_at"}
USERNAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$"
Role = Literal["owner", "operator", "viewer"]


class UsernameTaken(Exception):
    pass


class PgUsers:
    """gateway.users in Postgres (owned by the gateway, like refresh_tokens and audit_log)."""

    def __init__(self, db):
        self.db = db

    async def count(self) -> int:
        return int(await self.db.fetchval("SELECT count(*) FROM gateway.users") or 0)

    async def get(self, uid: int | None) -> dict | None:
        if uid is None:
            return None
        return await self.db.fetchrow(f"SELECT {COLUMNS} FROM gateway.users WHERE id = %s", (uid,))

    async def by_username(self, name: str) -> dict | None:
        return await self.db.fetchrow(f"SELECT {COLUMNS} FROM gateway.users WHERE lower(username) = lower(%s)", (name,))

    async def all(self) -> list[dict]:
        return await self.db.fetchall(
            f"SELECT {COLUMNS} FROM gateway.users ORDER BY disabled, CASE role WHEN 'owner' THEN 0 WHEN 'operator' THEN 1 "
            "ELSE 2 END, lower(username)")

    async def create(self, *, username: str, display_name: str, role: str, password_hash: str,
                     must_change_password: bool, created_by: str) -> dict:
        try:
            return await self.db.fetchrow(
                f"INSERT INTO gateway.users (username, display_name, role, password_hash, must_change_password, created_by) "
                f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {COLUMNS}",
                (username, display_name, role, password_hash, must_change_password, created_by))
        except UniqueViolation as exc:
            raise UsernameTaken(username) from exc

    async def update(self, uid: int, **fields) -> dict:
        bad = set(fields) - EDITABLE
        if bad:
            raise ValueError(f"not editable: {sorted(bad)}")
        sets = ", ".join(f"{k} = %s" for k in fields)
        values = [Jsonb(v) if k == "prefs" else v for k, v in fields.items()]
        return await self.db.fetchrow(
            f"UPDATE gateway.users SET {sets}, updated_at = now() WHERE id = %s RETURNING {COLUMNS}", (*values, uid))

    async def active_owners(self) -> int:
        return int(await self.db.fetchval(
            "SELECT count(*) FROM gateway.users WHERE role = 'owner' AND NOT disabled") or 0)

    async def revoke_sessions(self, uid: int) -> None:
        await self.db.execute(
            "UPDATE gateway.refresh_tokens SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (uid,))


async def bootstrap(users, username: str, password_hash: str) -> dict | None:
    """First start with user accounts: the .env admin becomes the first owner (same username and password)."""
    if await users.count():
        return None
    return await users.create(username=username, display_name=username, role="owner", password_hash=password_hash,
                              must_change_password=False, created_by="bootstrap (.env)")


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/api/v1/users", dependencies=[Depends(auth.OWNER)])


def public(u: dict) -> dict:
    return {k: u[k] for k in ("id", "username", "display_name", "role", "must_change_password", "disabled",
                              "created_at", "created_by", "last_login_at", "password_changed_at")}


async def _audit(request: Request, actor: str, subject: str, payload: dict) -> None:
    await request.app.state.db.execute(
        "INSERT INTO gateway.audit_log (command_id, actor, subject, payload, outcome) VALUES (%s, %s, %s, %s, 'done')",
        (f"{subject}-{uuid.uuid4().hex}", actor, subject, Jsonb(payload)))


@router.get("")
async def list_users(request: Request):
    return {"users": [public(u) for u in await request.app.state.users.all()]}


class NewUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=USERNAME)
    display_name: str = Field(default="", max_length=80)
    role: Role


@router.post("", status_code=201)
async def create_user(body: NewUser, request: Request, claims: dict = Depends(auth.OWNER_CHANGE)):
    temp = auth.temporary_password()
    try:
        user = await request.app.state.users.create(
            username=body.username, display_name=body.display_name.strip() or body.username, role=body.role,
            password_hash=auth.hash_password(temp), must_change_password=True, created_by=claims["sub"])
    except UsernameTaken:
        raise HTTPException(409, f"the username {body.username} is taken") from None
    await _audit(request, claims["sub"], "users.create", {"user": user["username"], "role": user["role"]})
    return {"user": public(user), "temporary_password": temp}


class UserChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=80)
    role: Role | None = None
    disabled: bool | None = None


async def _target(request: Request, uid: int) -> dict:
    user = await request.app.state.users.get(uid)
    if not user:
        raise HTTPException(404, f"no user #{uid}")
    return user


async def _keep_an_owner(request: Request, user: dict, change: UserChange) -> None:
    losing_owner = user["role"] == "owner" and not user["disabled"] and (
        (change.role is not None and change.role != "owner") or change.disabled is True)
    if losing_owner and await request.app.state.users.active_owners() <= 1:
        raise HTTPException(409, "this is the last active owner: make someone else an owner first")


@router.patch("/{uid}")
async def change_user(uid: int, body: UserChange, request: Request, claims: dict = Depends(auth.OWNER_CHANGE)):
    users, user = request.app.state.users, await _target(request, uid)
    if uid == claims["uid"] and (body.role not in (None, user["role"]) or body.disabled):
        raise HTTPException(409, "you cannot change your own role or disable yourself: ask another owner")
    await _keep_an_owner(request, user, body)
    fields: dict = {}
    if body.display_name is not None:
        fields["display_name"] = body.display_name.strip() or user["username"]
    access_changed = (body.role is not None and body.role != user["role"]) or (
        body.disabled is not None and body.disabled != user["disabled"])
    if body.role is not None:
        fields["role"] = body.role
    if body.disabled is not None:
        fields["disabled"] = body.disabled
    if access_changed:   # end their sessions: the new role (or the lock-out) applies at once
        fields["session_version"] = user["session_version"] + 1
        await users.revoke_sessions(uid)
    if not fields:
        return {"user": public(user)}
    updated = await users.update(uid, **fields)
    auth.forget(uid)
    change = {k: v for k, v in body.model_dump().items() if v is not None}
    await _audit(request, claims["sub"], "users.update", {"user": user["username"], **change,
                                                          "was": {k: user[k] for k in change}})
    return {"user": public(updated)}


@router.post("/{uid}/reset-password")
async def reset_password(uid: int, request: Request, claims: dict = Depends(auth.OWNER_CHANGE)):
    """A new one-time temporary password (shown once); the user must replace it at the next sign-in."""
    users, user = request.app.state.users, await _target(request, uid)
    if uid == claims["uid"]:
        raise HTTPException(409, "change your own password from your user menu")
    temp = auth.temporary_password()
    await users.revoke_sessions(uid)
    updated = await users.update(uid, password_hash=auth.hash_password(temp), must_change_password=True,
                                 session_version=user["session_version"] + 1, password_changed_at=datetime.now(UTC))
    auth.forget(uid)
    await _audit(request, claims["sub"], "users.reset_password", {"user": user["username"]})
    return {"user": public(updated), "temporary_password": temp}


USERNAME_RE = re.compile(USERNAME)
