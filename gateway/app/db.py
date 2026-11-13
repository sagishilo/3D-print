"""Small async Postgres helper for the gateway (psycopg 3 pool, autocommit, rows as dicts)."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class Db:
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10):
        self.pool = AsyncConnectionPool(
            dsn, min_size=min_size, max_size=max_size, open=False,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        await self.pool.close()

    async def execute(self, sql: str, params: tuple | dict = ()) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    async def fetchrow(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params)).fetchone()

    async def fetchall(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params)).fetchall()

    async def fetchval(self, sql: str, params: tuple | dict = ()) -> Any:
        row = await self.fetchrow(sql, params)
        return next(iter(row.values())) if row else None
