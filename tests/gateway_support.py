"""Shared fakes for the gateway tests: Postgres, NATS (JetStream, KV, ordered consumers), go2rtc, NATS monitoring.

Import this module only after ``pytest.importorskip`` of the gateway's dependencies.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
from argon2 import PasswordHasher

PASSWORD = "correct horse battery staple"
KEY = "test-key-0001"
os.environ.update(
    DATABASE_URL="postgresql://test@localhost/test",
    JWT_SECRET="test-secret-" + "x" * 40,
    ADMIN_USERNAME="admin",
    ADMIN_PASSWORD_HASH=PasswordHasher().hash(PASSWORD),
    COOKIE_SECURE="false",  # the test client talks plain http
    SITE_HOSTS="localhost, 192.168.50.10",
    EXTRA_ORIGINS="http://localhost:5173",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from app import auth  # noqa: E402
from app import main as gateway  # noqa: E402
from app import users as users_mod  # noqa: E402
from app.commands import COMMANDS, build_command, command_id_from, ws_topic_allowed  # noqa: E402, F401 - re-exported
from fastapi.testclient import TestClient  # noqa: E402
from nats.errors import TimeoutError as NatsTimeout  # noqa: E402
from nats.js.errors import NotFoundError  # noqa: E402


class FakeDb:
    def __init__(self):
        self.tokens: set[str] = set()
        self.audit: dict[str, dict] = {}
        self.rows: dict[str, list[dict]] = {}     # SQL substring -> rows for fetchall / fetchrow
        self.values: dict[str, object] = {}      # SQL substring -> value for fetchval

    async def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO gateway.refresh_tokens"):
            self.tokens.add(params[0])
        elif sql.startswith("INSERT INTO gateway.audit_log"):
            self.audit.setdefault(params[0], {"actor": params[1], "subject": params[2], "outcome": "pending",
                                              "payload": getattr(params[3], "obj", params[3])})
            if "'done'" in sql:
                self.audit[params[0]]["outcome"] = "done"
            elif len(params) > 4:
                self.audit[params[0]]["outcome"] = params[4]
        elif sql.startswith("UPDATE gateway.audit_log"):
            self.audit[params[1]]["outcome"] = params[0]
        elif sql.startswith("UPDATE gateway.refresh_tokens"):
            self.tokens.discard(params[0])
        return 1

    async def fetchall(self, sql, params=()):
        return next((rows for key, rows in self.rows.items() if key in sql), [])

    async def fetchrow(self, sql, params=()):
        if sql.startswith("UPDATE gateway.refresh_tokens") and "RETURNING" in sql:
            if params[0] in self.tokens:
                self.tokens.discard(params[0])
                return {"id": 1}
            return None
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    async def fetchval(self, sql, params=()):
        return next((v for key, v in self.values.items() if key in sql), 1)


def fake_msg(subject: str, stream: str, seq: int, data: dict, pending: int = 0):
    return SimpleNamespace(subject=subject, data=json.dumps(data).encode(),
                           metadata=SimpleNamespace(stream=stream, sequence=SimpleNamespace(stream=seq), num_pending=pending))


class FakeSub:
    def __init__(self, msgs=()):
        self.msgs = list(msgs)
        self.unsubscribed = False

    async def next_msg(self, timeout=1.0):
        if self.msgs:
            return self.msgs.pop(0)
        raise NatsTimeout

    async def unsubscribe(self):
        self.unsubscribed = True


def stream(name: str, subjects: list[str], first: int = 1, last: int = 10, storage: str = "file"):
    return SimpleNamespace(
        config=SimpleNamespace(name=name, subjects=subjects, storage=SimpleNamespace(value=storage), max_age=3600.0),
        state=SimpleNamespace(first_seq=first, last_seq=last, messages=max(last - first + 1, 0), bytes=1000,
                              consumer_count=1))


class FakeJs:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.headers: list[dict] = []
        self._ids: set[str] = set()
        self.subscribed: list[str] = []
        self.configs: list = []
        self.stored: dict[str, list] = {}            # subject -> messages a range read returns
        self.dlq: dict[int, bytes] = {}
        self.deleted: list[int] = []
        self.consumers: dict[str, list] = {}
        self.streams = {"EVENTS": stream("EVENTS", ["evt.>"]), "TELEMETRY": stream("TELEMETRY", ["tlm.>"], storage="memory"),
                        "COMMANDS": stream("COMMANDS", ["cmd.>"]), "DLQ": stream("DLQ", ["dlq.>"], 1, 0)}

    async def publish(self, subject, payload, headers=None, timeout=None):
        msg_id = headers["Nats-Msg-Id"]
        duplicate = msg_id in self._ids
        self._ids.add(msg_id)
        if not duplicate:
            self.published.append((subject, json.loads(payload)))
            self.headers.append(headers)
        return SimpleNamespace(stream="COMMANDS" if subject.startswith("cmd.") else "EVENTS", seq=len(self.published),
                               duplicate=duplicate)

    async def subscribe(self, subject, cb=None, ordered_consumer=False, config=None, **kw):
        self.subscribed.append(subject)
        self.configs.append(config)
        return FakeSub([] if cb else self.stored.get(subject, []))

    async def stream_info(self, name):
        return self.streams[name]

    async def streams_info(self):
        return list(self.streams.values())

    async def consumers_info(self, name):
        return self.consumers.get(name, [])

    async def get_msg(self, name, seq):
        if seq in self.dlq:
            return SimpleNamespace(seq=seq, data=self.dlq[seq])
        raise NotFoundError

    async def delete_msg(self, name, seq):
        self.dlq.pop(seq, None)
        self.deleted.append(seq)
        return True


class FakeBus:
    def __init__(self):
        self.js = FakeJs()
        self.nc = SimpleNamespace(is_connected=True)
        self.kv: dict[str, dict[str, bytes]] = {"heartbeats": {}, "printer_status": {}, "config": {}}

    async def kv_keys(self, bucket):
        return list(self.kv[bucket])

    async def kv_get(self, bucket, key):
        return self.kv[bucket].get(key)

    async def blob_put(self, bucket, name, data):
        self.kv.setdefault(f"obs:{bucket}", {})[name] = data


class FakeHttp:
    """go2rtc or the NATS monitoring API: path prefix -> JSON body."""

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = routes or {}

    async def get(self, path, **kw):
        for prefix, body in self.routes.items():
            if path.startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={})


class FakeUsers:
    """gateway.users in memory, with the same methods as app.users.PgUsers."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.revoked: list[int] = []
        self._next = 1

    async def count(self):
        return len(self.rows)

    async def get(self, uid):
        row = self.rows.get(uid)
        return dict(row) if row else None

    async def by_username(self, name):
        return next((dict(r) for r in self.rows.values() if r["username"].lower() == name.lower()), None)

    async def all(self):
        return [dict(r) for r in self.rows.values()]

    async def create(self, *, username, display_name, role, password_hash, must_change_password, created_by):
        if await self.by_username(username):
            raise users_mod.UsernameTaken(username)
        now = datetime.now(UTC)
        row = {"id": self._next, "username": username, "display_name": display_name, "role": role,
               "password_hash": password_hash, "must_change_password": must_change_password, "disabled": False,
               "session_version": 1, "prefs": {}, "created_at": now, "created_by": created_by, "updated_at": now,
               "last_login_at": None, "password_changed_at": None}
        self.rows[self._next] = row
        self._next += 1
        return dict(row)

    async def update(self, uid, **fields):
        bad = set(fields) - users_mod.EDITABLE
        if bad:
            raise ValueError(f"not editable: {sorted(bad)}")
        self.rows[uid].update(fields, updated_at=datetime.now(UTC))
        return dict(self.rows[uid])

    async def active_owners(self):
        return sum(1 for r in self.rows.values() if r["role"] == "owner" and not r["disabled"])

    async def revoke_sessions(self, uid):
        self.revoked.append(uid)


async def add_user(app, username: str, role: str, password: str = PASSWORD, must_change: bool = False) -> dict:
    """A user straight in the fake store (tests that need several roles)."""
    return await app.state.users.create(username=username, display_name=username.title(), role=role,
                                        password_hash=auth.hash_password(password), must_change_password=must_change,
                                        created_by="test")


@contextmanager
def gateway_client():
    @asynccontextmanager
    async def fake_lifespan(app):
        app.state.db, app.state.bus = FakeDb(), FakeBus()
        app.state.users = FakeUsers()
        await users_mod.bootstrap(app.state.users, "admin", os.environ["ADMIN_PASSWORD_HASH"])
        app.state.http = FakeHttp({"/api/streams": {"p1": {}, "voron-01": {}}, "/api": {"version": "1.9.15"}})
        app.state.nats_http = FakeHttp()
        app.state.ws_clients, app.state.started = 0, time.monotonic()
        gateway.limiter.reset()
        yield

    gateway.app.router.lifespan_context = fake_lifespan
    auth._failures.clear()
    auth._users.clear()
    with TestClient(gateway.app) as c:
        yield c


def login(c, password: str = PASSWORD):
    return c.post("/api/v1/auth/login", json={"username": "admin", "password": password})


def csrf(c) -> dict:
    return {"x-csrf-token": c.cookies.get("pf_csrf")}
