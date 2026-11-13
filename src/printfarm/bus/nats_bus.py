"""NATS JetStream implementation of the bus, and the ONLY place the JetStream topology is defined.

Agents, the gateway and the Finance agent all connect through ``NatsBus.connect``, so
every process agrees on the same streams; nothing else may create or update streams.

Streams (created idempotently on connect)
-----------------------------------------
EVENTS     evt.>   file,   limits retention, 30 days, 2 min duplicate window  -> fan-out
COMMANDS   cmd.>   file,   work-queue retention, 7 days                       -> one consumer per agent
TELEMETRY  tlm.>   memory, 1 hour, 3600 msgs per printer                       -> live dashboards / replay
DLQ        dlq.>   file,   90 days                                             -> operator inspection

Queries (``qry.>``) are core NATS request/reply and are never stored in a stream.

KV buckets
----------
printer_status  latest canonical PrinterState per printer (written by telemetry)
heartbeats      agent liveness, 30 s TTL
leases          single-writer locks (e.g. one scheduler instance), 60 s TTL
config          automation settings (written by the coordinator on cmd.coordinator.set_config)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import nats
from nats.errors import TimeoutError as NatsTimeout
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    KeyValueConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import KeyNotFoundError, NotFoundError

from printfarm.bus.base import DEFAULT_MAX_DELIVER, Handler, Outcome, Responder, backoff_for, deliver, durable_name
from printfarm.contracts import Envelope

log = logging.getLogger(__name__)

DAY = 24 * 3600

STREAMS = [
    StreamConfig(
        name="EVENTS", subjects=["evt.>"], storage=StorageType.FILE, retention=RetentionPolicy.LIMITS,
        max_age=30 * DAY, duplicate_window=120,
    ),
    StreamConfig(
        name="COMMANDS", subjects=["cmd.>"], storage=StorageType.FILE, retention=RetentionPolicy.WORK_QUEUE,
        max_age=7 * DAY, duplicate_window=120,
    ),
    StreamConfig(
        name="TELEMETRY", subjects=["tlm.>"], storage=StorageType.MEMORY, retention=RetentionPolicy.LIMITS,
        max_age=3600, max_msgs_per_subject=3600,
    ),
    StreamConfig(name="DLQ", subjects=["dlq.>"], storage=StorageType.FILE, max_age=90 * DAY),
]

KV_BUCKETS = [
    KeyValueConfig(bucket="printer_status", history=1),
    KeyValueConfig(bucket="heartbeats", history=1, ttl=30),
    KeyValueConfig(bucket="leases", history=1, ttl=60),
    KeyValueConfig(bucket="config", history=10),
]


class NatsBus:
    def __init__(self, nc: nats.NATS):
        self.nc = nc
        self.js = nc.jetstream()
        self._tasks: list[asyncio.Task] = []
        self._kv: dict = {}

    @classmethod
    async def connect(cls, url: str, name: str = "printfarm") -> NatsBus:
        nc = await nats.connect(url, name=name, max_reconnect_attempts=-1)
        bus = cls(nc)
        await bus._ensure_topology()
        return bus

    async def _ensure_topology(self) -> None:
        for cfg in STREAMS:
            try:
                await self.js.add_stream(cfg)
            except Exception:  # noqa: BLE001 - exists with different config -> update
                await self.js.update_stream(cfg)
        for kv in KV_BUCKETS:
            try:
                self._kv[kv.bucket] = await self.js.key_value(kv.bucket)
            except (NotFoundError, Exception):  # noqa: BLE001
                self._kv[kv.bucket] = await self.js.create_key_value(kv)

    # ------------------------------------------------------------- pub/sub

    async def publish(self, env: Envelope) -> None:
        await self.js.publish(env.nats_subject(), env.to_bytes(), headers={"Nats-Msg-Id": env.id})

    async def subscribe(
        self, consumer: str, subject_filter: str, handler: Handler, *, max_deliver: int = DEFAULT_MAX_DELIVER,
        from_start: bool = False,
    ) -> None:
        durable = durable_name(consumer, subject_filter)
        is_command = subject_filter.startswith("cmd.")
        cfg = ConsumerConfig(
            durable_name=durable,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=60,
            # +1: the final delivery is the one that dead-letters and acks.
            max_deliver=max_deliver + 1,
            # A brand-new event consumer starts at "now" unless it asks for the stored history
            # (from_start, e.g. the ledger); a durable always resumes where it left off.
            deliver_policy=DeliverPolicy.ALL if is_command or from_start else DeliverPolicy.NEW,
            filter_subject=subject_filter,
        )
        psub = await self.js.pull_subscribe(subject_filter, durable=durable, config=cfg)
        self._tasks.append(asyncio.create_task(self._pump(psub, consumer, handler, max_deliver), name=durable))

    async def _pump(self, psub, consumer: str, handler: Handler, max_deliver: int) -> None:
        while True:
            try:
                msgs = await psub.fetch(batch=10, timeout=1)
            except (TimeoutError, NatsTimeout):
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - connection blips; keep pumping
                log.warning("fetch failed for %s: %r", consumer, exc)
                await asyncio.sleep(1)
                continue
            for msg in msgs:  # sequential: preserves per-consumer ordering
                try:
                    env = Envelope.from_bytes(msg.data)
                except Exception as exc:  # noqa: BLE001 - poison message, cannot even parse
                    log.error("unparseable message on %s: %r", msg.subject, exc)
                    await msg.term()
                    continue
                attempt = msg.metadata.num_delivered
                outcome = await deliver(self, consumer, env, handler, attempt, max_deliver)
                if outcome == Outcome.RETRY:
                    await msg.nak(delay=backoff_for(attempt))
                else:
                    await msg.ack()

    # ------------------------------------------------------------- request/reply (queries)

    async def request(self, env: Envelope, timeout: float = 2.0) -> Envelope:
        resp = await self.nc.request(env.nats_subject(), env.to_bytes(), timeout=timeout)
        return Envelope.from_bytes(resp.data)

    async def serve(self, subject: str, responder: Responder) -> None:
        async def _cb(msg):
            try:
                reply = await responder(Envelope.from_bytes(msg.data))
                await msg.respond(reply.to_bytes())
            except Exception as exc:  # noqa: BLE001
                log.error("query handler for %s failed: %r", subject, exc)

        # Queue group: several replicas of an agent share the load.
        await self.nc.subscribe(subject, queue=subject, cb=_cb)

    # ------------------------------------------------------------- KV

    async def kv_put(self, bucket: str, key: str, value: bytes) -> None:
        await self._kv[bucket].put(key, value)

    async def kv_get(self, bucket: str, key: str) -> bytes | None:
        try:
            entry = await self._kv[bucket].get(key)
            return entry.value
        except KeyNotFoundError:
            return None

    async def kv_keys(self, bucket: str) -> list[str]:
        try:
            return list(await self._kv[bucket].keys())
        except Exception:  # noqa: BLE001 - nats-py raises NoKeysError on empty bucket
            return []

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await self.nc.drain()
