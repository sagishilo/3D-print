"""PostgreSQL access: connection pool, migrations, outbox and inbox helpers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from importlib import resources

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from printfarm.bus.base import Bus
from printfarm.contracts import Envelope

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

    async def migrate(self) -> None:
        async with self.tx() as conn:
            # Serialise concurrent migrators (several agents may start together).
            await conn.execute("SELECT pg_advisory_xact_lock(4242)")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS public.schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())"
            )
            done = {r["name"] for r in await (await conn.execute("SELECT name FROM public.schema_migrations")).fetchall()}
            files = sorted(
                (p for p in resources.files("printfarm.store").joinpath("migrations").iterdir() if p.name.endswith(".sql")),
                key=lambda p: p.name,
            )
            for f in files:
                if f.name in done:
                    continue
                log.info("applying migration %s", f.name)
                await conn.execute(f.read_text())
                await conn.execute("INSERT INTO public.schema_migrations (name) VALUES (%s)", (f.name,))


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
