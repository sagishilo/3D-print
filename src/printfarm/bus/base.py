"""Transport-neutral bus interface plus the shared delivery policy.

Delivery contract every implementation must honour
--------------------------------------------------
* At-least-once delivery to each durable consumer. Exactly-once *effects*
  come from the consumer's inbox table (see ``store/inbox.py``).
* Publish de-duplication on ``Envelope.id`` (JetStream ``Nats-Msg-Id``).
* A handler that raises is retried with backoff; after ``max_deliver``
  attempts the message is wrapped in ``DeadLettered`` and published to
  ``dlq.*`` and the original is acknowledged so the stream keeps moving.
* Commands past their ``deadline`` are acknowledged and dropped, never run.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from printfarm.contracts import Envelope, Kind, new_message
from printfarm.contracts.messages import DeadLettered

log = logging.getLogger(__name__)

Handler = Callable[[Envelope], Awaitable[None]]
Responder = Callable[[Envelope], Awaitable[Envelope]]

DEFAULT_MAX_DELIVER = 5
BACKOFF_S = (1, 5, 15, 60)


class Bus(Protocol):
    async def publish(self, env: Envelope) -> None: ...

    async def subscribe(
        self, consumer: str, subject_filter: str, handler: Handler, *, max_deliver: int = DEFAULT_MAX_DELIVER
    ) -> None: ...

    async def request(self, env: Envelope, timeout: float = 2.0) -> Envelope: ...

    async def serve(self, subject: str, responder: Responder) -> None: ...

    async def kv_put(self, bucket: str, key: str, value: bytes) -> None: ...

    async def kv_get(self, bucket: str, key: str) -> bytes | None: ...

    async def kv_keys(self, bucket: str) -> list[str]: ...

    async def close(self) -> None: ...


class Outcome:
    ACK = "ack"
    RETRY = "retry"
    DEAD = "dead"


async def deliver(bus: Bus, consumer: str, env: Envelope, handler: Handler, attempt: int, max_deliver: int) -> str:
    """Run ``handler`` once and decide what the transport should do with the message."""
    if env.kind == Kind.COMMAND and env.is_expired():
        log.warning("dropping expired command %s %s", env.type, env.id)
        return Outcome.ACK
    try:
        await handler(env)
        return Outcome.ACK
    except Exception as exc:  # noqa: BLE001 - any handler failure is retried / dead-lettered
        if attempt < max_deliver:
            log.warning("%s failed on %s (attempt %d/%d): %r", consumer, env.type, attempt, max_deliver, exc)
            return Outcome.RETRY
        log.error("%s dead-lettering %s %s: %r", consumer, env.type, env.id, exc)
        dead = new_message(
            DeadLettered(
                original_subject=env.nats_subject(),
                original_id=env.id,
                consumer=consumer,
                error=repr(exc)[:2000],
                deliveries=attempt,
                original=env.model_dump(mode="json"),
            ),
            source=f"/consumers/{consumer}",
            cause=env,
        )
        await bus.publish(dead)
        return Outcome.DEAD


def backoff_for(attempt: int) -> float:
    return BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)]


def durable_name(consumer: str, subject_filter: str) -> str:
    safe = subject_filter.replace(".", "_").replace("*", "STAR").replace(">", "ALL")
    return f"{consumer}__{safe}"
