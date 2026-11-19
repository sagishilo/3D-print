"""Recovery when no owner can sign in (forgotten password, every owner disabled): run on the PC that hosts the stack.

    docker compose exec gateway python -m app.recover <username>

Makes that account an enabled owner with a one-time temporary password (printed once), signs out its sessions and
writes the recovery to the audit log. Only someone with access to this PC's Docker can run it, which is the same
trust as reading .env.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from psycopg.types.json import Jsonb

from .auth import hash_password, temporary_password


async def recover(users, db, username: str) -> str:
    user = await users.by_username(username)
    if not user:
        raise LookupError(f"no user named {username!r}")
    temp = temporary_password()
    await users.revoke_sessions(user["id"])
    await users.update(user["id"], role="owner", disabled=False, must_change_password=True,
                       password_hash=hash_password(temp), session_version=user["session_version"] + 1)
    await db.execute(
        "INSERT INTO gateway.audit_log (command_id, actor, subject, payload, outcome) VALUES (%s, %s, %s, %s, 'done')",
        (f"users.recover-{uuid.uuid4().hex}", "recovery (console)", "users.recover", Jsonb({"user": user["username"]})))
    return temp


async def main(username: str) -> None:
    from .config import settings
    from .db import Db
    from .users import PgUsers

    db = Db(settings.database_url, max_size=1)
    await db.open()
    try:
        temp = await recover(PgUsers(db), db, username)
    except LookupError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        await db.close()
    print(f"{username} is an owner again. One-time password (choose your own at sign-in): {temp}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.recover <username>")
    asyncio.run(main(sys.argv[1]))
