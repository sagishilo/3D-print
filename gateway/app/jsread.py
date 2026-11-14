"""Reading JetStream history for the dashboard (feed replay, telemetry history) with short-lived ordered consumers."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Any

from nats.errors import TimeoutError as NatsTimeout
from nats.js.api import ConsumerConfig, DeliverPolicy

# Which stream holds a topic, by its root token (the same roots the WebSocket allows).
STREAMS = {"evt": "EVENTS", "tlm": "TELEMETRY", "dlq": "DLQ"}


def stream_for(topic: str) -> str:
    return STREAMS[topic.split(".", 1)[0]]


def start_config(*, seq: int | None = None, since: datetime | None = None) -> ConsumerConfig:
    """Where a consumer starts: a stream sequence (resume), a point in time (replay) or only new messages."""
    if seq is not None:
        return ConsumerConfig(deliver_policy=DeliverPolicy.BY_START_SEQUENCE, opt_start_seq=max(seq, 1))
    if since is not None:
        return ConsumerConfig(deliver_policy=DeliverPolicy.BY_START_TIME, opt_start_time=since)
    return ConsumerConfig(deliver_policy=DeliverPolicy.NEW)


def decode(data: bytes) -> Any:
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": data.decode(errors="replace")}


def as_item(msg) -> dict:
    """A stored message as the dashboard sees it: topic, stream, stream sequence and the envelope."""
    meta = msg.metadata
    return {"topic": msg.subject, "stream": meta.stream, "seq": meta.sequence.stream, "data": decode(msg.data)}


async def read_range(js, subject: str, *, since: datetime | None = None, seq: int | None = None, limit: int = 2000,
                     first_timeout: float = 1.0, idle_timeout: float = 0.5) -> list:
    """Stored messages on ``subject`` from ``since`` / ``seq`` up to now, oldest first, at most ``limit``."""
    sub = await js.subscribe(subject, ordered_consumer=True, config=start_config(seq=seq, since=since))
    out: list = []
    try:
        timeout = first_timeout
        while len(out) < limit:
            try:
                msg = await sub.next_msg(timeout=timeout)
            except (NatsTimeout, TimeoutError):
                break  # nothing (more) stored in the range
            out.append(msg)
            if msg.metadata.num_pending == 0:
                break  # caught up with the stream
            timeout = idle_timeout
    finally:
        with contextlib.suppress(Exception):
            await sub.unsubscribe()
    return out
