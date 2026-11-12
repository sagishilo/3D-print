"""In-process bus with the same delivery contract as NatsBus. For unit tests only."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

from printfarm.bus.base import DEFAULT_MAX_DELIVER, Handler, Outcome, Responder, deliver
from printfarm.contracts import Envelope, subject_matches


@dataclass
class _Sub:
    consumer: str
    pattern: str
    handler: Handler
    max_deliver: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None


class MemoryBus:
    def __init__(self) -> None:
        self.subs: list[_Sub] = []
        self.published: list[Envelope] = []
        self._seen: set[str] = set()
        self._responders: dict[str, Responder] = {}
        self._kv: dict[str, dict[str, bytes]] = {}

    async def publish(self, env: Envelope) -> None:
        if env.id in self._seen:  # mirrors the JetStream duplicate window
            return
        self._seen.add(env.id)
        self.published.append(env)
        subj = env.nats_subject()
        for sub in self.subs:
            if subject_matches(sub.pattern, subj):
                sub.queue.put_nowait(env)

    async def subscribe(self, consumer, subject_filter, handler, *, max_deliver=DEFAULT_MAX_DELIVER) -> None:
        sub = _Sub(consumer, subject_filter, handler, max_deliver)
        sub.task = asyncio.create_task(self._pump(sub))
        self.subs.append(sub)

    async def _pump(self, sub: _Sub) -> None:
        while True:
            env = await sub.queue.get()
            attempt = 1
            while await deliver(self, sub.consumer, env, sub.handler, attempt, sub.max_deliver) == Outcome.RETRY:
                attempt += 1
            sub.queue.task_done()

    async def drain(self) -> None:
        """Wait until every queued message has been handled (including cascades)."""
        for _ in range(50):
            await asyncio.sleep(0)
            if all(s.queue.empty() for s in self.subs):
                await asyncio.gather(*(s.queue.join() for s in self.subs))
                if all(s.queue.empty() for s in self.subs):
                    return

    async def request(self, env: Envelope, timeout: float = 2.0) -> Envelope:
        return await asyncio.wait_for(self._responders[env.nats_subject()](env), timeout)

    async def serve(self, subject: str, responder: Responder) -> None:
        self._responders[subject] = responder

    async def kv_put(self, bucket: str, key: str, value: bytes) -> None:
        self._kv.setdefault(bucket, {})[key] = value

    async def kv_get(self, bucket: str, key: str) -> bytes | None:
        return self._kv.get(bucket, {}).get(key)

    async def kv_keys(self, bucket: str) -> list[str]:
        return list(self._kv.get(bucket, {}))

    async def close(self) -> None:
        for s in self.subs:
            if s.task:
                s.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await s.task
