"""Base class for every agent.

An agent = a set of handlers bound to subject filters + an outbox relay + a
heartbeat. Each handler runs inside ONE database transaction that also
claims the message in the inbox and stores outgoing messages in the outbox:

    BEGIN
      INSERT inbox(consumer, msg.id)   -- duplicate? -> skip handler, COMMIT
      <handler business writes>
      INSERT outbox(...)                -- via self.emit()
    COMMIT
    ack to JetStream

so the agent's state change and the messages it sends either all happen or
none happen, and a redelivered message changes nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from printfarm.bus.base import Bus, durable_name
from printfarm.config import FleetCfg, Settings
from printfarm.contracts import Envelope, Payload, new_message
from printfarm.store import Database, OutboxRelay, inbox_claim, outbox_add

TxHandler = Callable[[Any, Envelope, Any, AsyncConnection], Awaitable[None]]

# Callbacks a handler registered with Agent.after_commit(); one list per handler invocation.
_AFTER_COMMIT: contextvars.ContextVar[list[Callable[[], None]] | None] = contextvars.ContextVar("after_commit", default=None)


def on(subject_filter: str, *, from_start: bool = False):
    """Mark a method as handler: ``async def h(self, env, payload, conn)``.

    ``from_start=True``: when the consumer is created for the first time it reads the
    stream's stored history instead of starting at "now" (e.g. the ledger books past events).
    """

    def deco(fn: TxHandler) -> TxHandler:
        fn.__subject_filter__ = subject_filter  # type: ignore[attr-defined]
        fn.__from_start__ = from_start  # type: ignore[attr-defined]
        return fn

    return deco


class Agent:
    name: str = "agent"

    def __init__(self, bus: Bus, db: Database, settings: Settings, fleet: FleetCfg):
        self.bus, self.db, self.settings, self.fleet = bus, db, settings, fleet
        self.log = logging.getLogger(f"agent.{self.name}")
        self.relay = OutboxRelay(db, bus, self.name)
        self._tasks: set[asyncio.Task] = set()
        self._started = time.time()

    @property
    def source(self) -> str:
        return f"/agents/{self.name}"

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await self.on_start()
        for attr in dir(type(self)):
            fn = getattr(type(self), attr)
            filt = getattr(fn, "__subject_filter__", None)
            if filt:
                await self.bus.subscribe(self.name, filt, self._wrap(getattr(self, attr), filt),
                                         from_start=getattr(fn, "__from_start__", False))
        self.spawn(self.relay.run())
        self.spawn(self._heartbeat())

    async def on_start(self) -> None:
        """Seed state, open device connections, start loops."""

    def spawn(self, coro) -> asyncio.Task:
        """Run a background task owned by this agent (cancelled on stop; forgotten once finished)."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def after_commit(self, fn: Callable[[], None]) -> None:
        """Inside a handler: run ``fn`` once the handler's transaction has committed (never if it rolls back)."""
        pending = _AFTER_COMMIT.get()
        if pending is None:  # not inside a handler: nothing to wait for
            fn()
        else:
            pending.append(fn)

    async def stop(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _heartbeat(self) -> None:
        while True:
            beat = {"agent": self.name, "at": datetime.now().isoformat(), "uptime_s": int(time.time() - self._started)}
            with contextlib.suppress(Exception):
                await self.bus.kv_put("heartbeats", self.name, json.dumps(beat).encode())
            await asyncio.sleep(10)

    # ------------------------------------------------------------ messaging

    def _wrap(self, method, filt: str):
        consumer = durable_name(self.name, filt)

        async def handler(env: Envelope) -> None:
            payload = env.payload()
            pending: list[Callable[[], None]] = []
            token = _AFTER_COMMIT.set(pending)
            try:
                async with self.db.tx() as conn:
                    if not await inbox_claim(conn, consumer, env.id):
                        self.log.debug("duplicate %s %s ignored", env.type, env.id)
                        return
                    await method(env, payload, conn)
            finally:
                _AFTER_COMMIT.reset(token)
            self.relay.wakeup.set()
            for fn in pending:  # reached only after a successful commit
                fn()

        return handler

    async def emit(self, conn: AsyncConnection, payload: Payload, cause: Envelope | None = None, **kw) -> Envelope:
        """Queue a message in the outbox, inside the caller's transaction."""
        env = new_message(payload, source=self.source, cause=cause, **kw)
        await outbox_add(conn, self.name, env)
        self.log.info("emit %s corr=%s", payload.subject, env.correlationid)
        return env

    async def emit_now(self, payload: Payload, cause: Envelope | None = None, **kw) -> Envelope:
        """Emit outside a handler (timers, device callbacks) - still via the outbox."""
        async with self.db.tx() as conn:
            env = await self.emit(conn, payload, cause, **kw)
        self.relay.wakeup.set()
        return env
