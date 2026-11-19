"""gateway.users on a real Postgres (migration 012 + app.users.PgUsers): the bootstrap owner, case-insensitive
unique usernames, updates, sessions tied to a user. Runs when TEST_DATABASE_URL is set (the CI e2e job)."""

from __future__ import annotations

import importlib
import os
import uuid

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")


@pytest.fixture
async def users():
    import gateway_support  # noqa: F401 - puts gateway/ on the path

    from printfarm.store.db import Database

    database = Database(DB_URL)
    await database.open()
    await database.migrate()
    await database.close()
    db = importlib.import_module("app.db").Db(DB_URL)
    await db.open()
    await db.execute("TRUNCATE gateway.users CASCADE")
    yield importlib.import_module("app.users").PgUsers(db), db
    await db.execute("TRUNCATE gateway.users CASCADE")
    await db.close()


async def test_users_in_postgres(users):
    store, db = users
    mod = importlib.import_module("app.users")
    owner = await mod.bootstrap(store, "admin", "$argon2id$hash")
    assert owner["role"] == "owner" and owner["created_by"] == "bootstrap (.env)"
    assert await mod.bootstrap(store, "admin", "x") is None            # only once
    noa = await store.create(username="Noa", display_name="Noa", role="operator", password_hash="h",
                             must_change_password=True, created_by="admin")
    assert (await store.by_username("NOA"))["id"] == noa["id"]
    with pytest.raises(mod.UsernameTaken):
        await store.create(username="noa", display_name="", role="viewer", password_hash="h",
                           must_change_password=False, created_by="admin")
    upd = await store.update(noa["id"], role="viewer", prefs={"theme": "light"}, session_version=2)
    assert upd["role"] == "viewer" and upd["prefs"] == {"theme": "light"} and upd["session_version"] == 2
    assert await store.active_owners() == 1 and [u["username"] for u in await store.all()] == ["admin", "Noa"]
    # a session belongs to its user; revoking ends only theirs
    for uid in (owner["id"], noa["id"]):
        await db.execute("INSERT INTO gateway.refresh_tokens (token_hash, expires_at, user_id) "
                         "VALUES (%s, now() + interval '1 day', %s)", (uuid.uuid4().hex, uid))
    await store.revoke_sessions(noa["id"])
    open_ = await db.fetchall("SELECT user_id FROM gateway.refresh_tokens WHERE revoked_at IS NULL AND user_id IS NOT NULL")
    assert [r["user_id"] for r in open_] == [owner["id"]]
    with pytest.raises(ValueError):
        await store.update(noa["id"], id=5)
