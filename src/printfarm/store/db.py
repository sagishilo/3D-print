"""PostgreSQL access: connection pool, migrations, outbox and inbox helpers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from printfarm.bus.base import Bus
from printfarm.contracts import Envelope
from printfarm.store import schema

log = logging.getLogger(__name__)


class Database:
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5):
        self.pool = AsyncConnectionPool(
            dsn, min_size=min_size, max_size=max_size, open=False, kwargs={"row_factory": dict_row}
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        await self.pool.close()

    @contextlib.asynccontextmanager
    async def tx(self) -> AsyncIterator[AsyncConnection]:
        """One transaction. Business writes, inbox claim and outbox insert all go through it."""
        async with self.pool.connection() as conn, conn.transaction():
            yield conn

    async def migration_plan(self) -> schema.Plan:
        async with self.tx() as conn:
            return await _plan(conn)

    async def migrate(self) -> schema.Plan:
        """Apply pending migrations (one transaction, one migrator at a time). Refuses when an applied migration
        was edited or the database is newer than this build: nothing is applied then."""
        async with self.tx() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (schema.LOCK_ID,))
            await conn.execute(schema.TABLE_SQL)
            plan = await _plan(conn)
            if not plan.ok:
                raise schema.MigrationError("; ".join(plan.problems))
            for m in plan.adopt:
                await conn.execute("UPDATE public.schema_migrations SET checksum = %s WHERE name = %s", (m.checksum, m.name))
            for m in plan.pending:
                log.info("applying migration %s", m.name)
                t0 = time.monotonic()
                await conn.execute(m.sql)
                await conn.execute(
                    "INSERT INTO public.schema_migrations (name, checksum, duration_ms) VALUES (%s, %s, %s)",
                    (m.name, m.checksum, int((time.monotonic() - t0) * 1000)))
            return plan


async def _plan(conn: AsyncConnection) -> schema.Plan:
    exists = await (await conn.execute("SELECT to_regclass('public.schema_migrations') AS t")).fetchone()
    applied: dict[str, str | None] = {}
    if exists and exists["t"]:
        cols = {r["column_name"] for r in await (await conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'schema_migrations'")).fetchall()}
        digest = "checksum" if "checksum" in cols else "NULL AS checksum"
        sql = f"SELECT name, {digest} FROM public.schema_migrations"
        applied = {r["name"]: r["checksum"] for r in await (await conn.execute(sql)).fetchall()}
    return schema.plan(schema.bundled(), applied)


# ------------------------------------------------------------------ inbox / outbox


async def inbox_claim(conn: AsyncConnection, consumer: str, message_id: str) -> bool:
    """True if this consumer has not processed the message before (row inserted)."""
    cur = await conn.execute(
        "INSERT INTO messaging.inbox (consumer, message_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (consumer, message_id),
    )
    return cur.rowcount == 1


async def outbox_add(conn: AsyncConnection, producer: str, env: Envelope) -> None:
    await conn.execute(
        "INSERT INTO messaging.outbox (producer, message_id, subject, envelope) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (message_id) DO NOTHING",
        (producer, env.id, env.nats_subject(), Jsonb(env.model_dump(mode="json", exclude_none=True))),
    )


class OutboxRelay:
    """Moves committed outbox rows of one producer onto the bus, in order.

    ``FOR UPDATE SKIP LOCKED`` lets several replicas of the same agent relay
    safely. Publishing is idempotent (Nats-Msg-Id = envelope id), so a crash
    between publish and the UPDATE only causes a harmless duplicate publish.
    """

    def __init__(self, db: Database, bus: Bus, producer: str, interval: float = 0.25, batch: int = 100):
        self.db, self.bus, self.producer = db, bus, producer
        self.interval, self.batch = interval, batch
        self.wakeup = asyncio.Event()

    async def run(self) -> None:
        while True:
            try:
                n = await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - NATS or DB down: retry later
                log.warning("outbox relay %s: %r", self.producer, exc)
                n = 0
            if n < self.batch:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.wakeup.wait(), self.interval)
                self.wakeup.clear()

    async def flush(self) -> int:
        async with self.db.tx() as conn:
            rows = await (
                await conn.execute(
                    "SELECT seq, envelope FROM messaging.outbox WHERE producer = %s AND published_at IS NULL "
                    "ORDER BY seq LIMIT %s FOR UPDATE SKIP LOCKED",
                    (self.producer, self.batch),
                )
            ).fetchall()
            for row in rows:
                await self.bus.publish(Envelope.model_validate(row["envelope"]))
            if rows:
                await conn.execute(
                    "UPDATE messaging.outbox SET published_at = now() WHERE seq = ANY(%s)", ([r["seq"] for r in rows],)
                )
            return len(rows)
