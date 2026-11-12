"""CloudEvents 1.0 envelope (structured JSON mode) shared by every agent.

Every message on the bus - event, command or reply - is one ``Envelope``.
Payloads are typed Pydantic models registered in ``registry.py``; the
envelope carries them as a plain ``data`` dict so the bus layer never needs
to know payload types.

CloudEvents extension attribute names must be lowercase alphanumerics, so the
tracing fields are spelled ``correlationid`` / ``causationid`` etc.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

# Namespace for deterministic ids (uuid5) of state-derived events.
ID_NAMESPACE = uuid.UUID("5b1f6c2e-9a43-4f0e-8d7a-3c2b1a0f9e88")


class Kind(StrEnum):
    EVENT = "event"      # a fact that happened; fan-out to any subscriber
    COMMAND = "command"  # a request for exactly one agent to act
    REPLY = "reply"      # answer to a query (core NATS request/reply)


class Payload(BaseModel):
    """Base class for every message payload.

    Subclasses declare where they travel and how they are versioned. The
    registry derives the CloudEvents ``type`` from these.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: ClassVar[str]           # NATS subject, e.g. "evt.fleet.job.completed"
    kind: ClassVar[Kind]
    version: ClassVar[int] = 1

    @classmethod
    def event_type(cls) -> str:
        # "evt.fleet.job.completed" -> "io.printfarm.fleet.job.completed.v1"
        tail = cls.subject.split(".", 1)[1]
        return f"io.printfarm.{tail}.v{cls.version}"


def _now() -> datetime:
    return datetime.now(UTC)


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- CloudEvents required attributes ---
    specversion: str = "1.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str                        # "/agents/fleet"
    type: str                          # "io.printfarm.fleet.job.completed.v1"
    # --- CloudEvents optional attributes ---
    subject: str | None = None         # the entity the message is about (order id, printer id, ...)
    time: datetime = Field(default_factory=_now)
    datacontenttype: str = "application/json"
    # --- extensions ---
    kind: Kind
    correlationid: str                 # saga / business transaction id; constant across a workflow
    causationid: str | None = None     # id of the message that directly caused this one
    idempotencykey: str | None = None  # business-level dedupe key (optional)
    deadline: datetime | None = None   # commands: do not act after this instant
    traceparent: str | None = None     # W3C trace context for OpenTelemetry
    data: dict[str, Any]

    # NATS subject is transport metadata, not part of the CloudEvent itself.
    def nats_subject(self) -> str:
        from printfarm.contracts.registry import subject_for_type

        subj = subject_for_type(self.type)
        # Telemetry is partitioned per printer: tlm.<printer_id>.state
        if subj.startswith("tlm.") and self.subject:
            return subj.replace("*", self.subject)
        return subj

    def payload(self) -> Payload:
        from printfarm.contracts.registry import model_for_type

        return model_for_type(self.type).model_validate(self.data)

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.deadline is not None and (now or _now()) > self.deadline

    def to_bytes(self) -> bytes:
        return self.model_dump_json(exclude_none=True).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> Envelope:
        return cls.model_validate_json(raw)


def deterministic_id(*parts: str) -> str:
    """Stable id for events derived from observed state.

    Telemetry may observe "print finished" twice (reconnect, restart). Giving
    both observations the same id lets JetStream's duplicate window and the
    consumers' inbox drop the second one.
    """
    return str(uuid.uuid5(ID_NAMESPACE, ":".join(parts)))


def new_message(
    payload: Payload,
    *,
    source: str,
    correlationid: str | None = None,
    cause: Envelope | None = None,
    subject: str | None = None,
    id: str | None = None,
    idempotencykey: str | None = None,
    deadline: datetime | None = None,
) -> Envelope:
    """Build an envelope. If ``cause`` is given, correlation flows from it."""
    msg_id = id or str(uuid.uuid4())
    corr = correlationid or (cause.correlationid if cause else None) or msg_id
    return Envelope(
        id=msg_id,
        source=source,
        type=payload.event_type(),
        subject=subject,
        kind=payload.kind,
        correlationid=corr,
        causationid=cause.id if cause else None,
        idempotencykey=idempotencykey,
        deadline=deadline,
        traceparent=cause.traceparent if cause else None,
        data=payload.model_dump(mode="json"),
    )
