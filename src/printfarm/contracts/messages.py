"""Typed payloads for every message on the bus.

Naming rules
------------
* Events:    ``evt.<domain>.<entity>.<past-tense-verb>``  (JetStream stream EVENTS)
* Commands:  ``cmd.<owning-agent>.<imperative>``           (JetStream stream COMMANDS, work queue)
* Telemetry: ``tlm.<printer_id>.state``                    (JetStream stream TELEMETRY, memory, 1h)
* Queries:   ``qry.<owning-agent>.<noun>``                 (core NATS request/reply, not persisted)

A breaking change to a payload means a new class with ``version = 2`` that is
published side by side with v1 until every consumer has migrated.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import Field

from printfarm.contracts.envelope import Kind, Payload

# ---------------------------------------------------------------- shared types


class PrinterStatus(StrEnum):
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"
    OFFLINE = "offline"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"  # the telemetry agent pauses the printer on its own


class Event(Payload):
    kind: ClassVar[Kind] = Kind.EVENT


class Command(Payload):
    kind: ClassVar[Kind] = Kind.COMMAND


class Reply(Payload):
    kind: ClassVar[Kind] = Kind.REPLY


# ---------------------------------------------------------------- orders / coordinator


class OrderReceived(Event):
    subject: ClassVar[str] = "evt.order.order.received"
    order_id: str
    customer_ref: str
    product: str = "nameplate"
    text: str = Field(max_length=40)
    material: str = "PLA"
    color: str = "black"
    quantity: int = Field(default=1, ge=1, le=50)
    due_by: datetime | None = None


class OrderCompleted(Event):
    subject: ClassVar[str] = "evt.order.order.completed"
    order_id: str


class OrderNeedsAttention(Event):
    subject: ClassVar[str] = "evt.order.order.needs_attention"
    order_id: str
    state: str
    reason: str


class ApprovalRequested(Event):
    subject: ClassVar[str] = "evt.approval.request.created"
    approval_id: str
    action: str          # e.g. "inventory.reorder", "social.reply", "printer.cancel"
    requested_by: str
    summary: str
    details: dict


class DecideApproval(Command):
    """Sent by a human (CLI / dashboard). Only the coordinator turns it into ApprovalDecided."""

    subject: ClassVar[str] = "cmd.coordinator.decide_approval"
    approval_id: str
    approved: bool
    decided_by: str
    note: str = ""


class ApprovalDecided(Event):
    subject: ClassVar[str] = "evt.approval.request.decided"
    approval_id: str
    action: str
    approved: bool
    decided_by: str
    note: str = ""


# ---------------------------------------------------------------- personalization


class GenerateNameplate(Command):
    subject: ClassVar[str] = "cmd.personalization.generate_nameplate"
    order_id: str
    text: str = Field(max_length=40)
    material: str
    color: str
    width_mm: float = 120.0
    height_mm: float = 35.0
    thickness_mm: float = 3.0
    text_height_mm: float = 1.6


class ModelReady(Event):
    subject: ClassVar[str] = "evt.personalization.model.ready"
    order_id: str
    artifact_uri: str            # where the printable file lives
    source_uri: str              # parametric source (.scad) for re-generation
    footprint_mm: tuple[float, float]
    est_grams: float
    est_minutes: float
    sliced: bool                 # False => estimates only, slicer CLI not installed


class ModelFailed(Event):
    subject: ClassVar[str] = "evt.personalization.model.failed"
    order_id: str
    reason: str


# ---------------------------------------------------------------- fleet


class EnqueueJob(Command):
    subject: ClassVar[str] = "cmd.fleet.enqueue_job"
    job_id: str
    order_id: str
    artifact_uri: str
    material: str
    color: str
    footprint_mm: tuple[float, float]
    est_grams: float
    est_minutes: float
    due_by: datetime | None = None
    priority: int = 0


class JobQueued(Event):
    subject: ClassVar[str] = "evt.fleet.job.queued"
    job_id: str
    order_id: str


class PlateScheduled(Event):
    subject: ClassVar[str] = "evt.fleet.plate.scheduled"
    plate_id: str
    printer_id: str
    job_ids: list[str]
    material: str
    color: str
    est_grams: float
    est_minutes: float


class PlateFinished(Event):
    subject: ClassVar[str] = "evt.fleet.plate.finished"
    plate_id: str
    printer_id: str
    material: str
    color: str
    succeeded: bool
    grams_used: float           # estimate scaled by progress for failed plates


class JobStarted(Event):
    subject: ClassVar[str] = "evt.fleet.job.started"
    job_id: str
    order_id: str
    plate_id: str
    printer_id: str


class MarkBedCleared(Command):
    """Operator confirms the finished plate was removed and the bed is ready."""

    subject: ClassVar[str] = "cmd.fleet.mark_bed_cleared"
    printer_id: str
    operator: str


class JobCompleted(Event):
    subject: ClassVar[str] = "evt.fleet.job.completed"
    job_id: str
    order_id: str
    plate_id: str


class JobFailed(Event):
    subject: ClassVar[str] = "evt.fleet.job.failed"
    job_id: str
    order_id: str
    plate_id: str
    reason: str


# ---------------------------------------------------------------- printer gateway (telemetry agent)


class StartPrint(Command):
    subject: ClassVar[str] = "cmd.telemetry.start_print"
    printer_id: str
    plate_id: str
    artifact_uris: list[str]


class PausePrint(Command):
    subject: ClassVar[str] = "cmd.telemetry.pause_print"
    printer_id: str
    reason: str


class PrinterState(Event):
    """Canonical, vendor-neutral printer state. Sampled to tlm.<printer_id>.state."""

    subject: ClassVar[str] = "tlm.*.state"
    printer_id: str
    vendor: str
    status: PrinterStatus
    plate_id: str | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    layer: int | None = None
    total_layers: int | None = None
    nozzle_temp: float | None = None
    nozzle_target: float | None = None
    bed_temp: float | None = None
    bed_target: float | None = None
    remaining_s: int | None = None
    error_code: str | None = None
    observed_at: datetime


class PrintStarted(Event):
    subject: ClassVar[str] = "evt.printer.print.started"
    printer_id: str
    plate_id: str


class PrintFinished(Event):
    subject: ClassVar[str] = "evt.printer.print.finished"
    printer_id: str
    plate_id: str
    succeeded: bool
    progress: float
    reason: str = ""


class FailureDetected(Event):
    subject: ClassVar[str] = "evt.printer.failure.detected"
    printer_id: str
    plate_id: str | None
    detector: str
    severity: Severity
    message: str
    action_taken: str            # "paused" | "none"


class PrinterConnectivity(Event):
    subject: ClassVar[str] = "evt.printer.connection.changed"
    printer_id: str
    online: bool


# ---------------------------------------------------------------- inventory


class FilamentConsumed(Event):
    subject: ClassVar[str] = "evt.inventory.filament.consumed"
    spool_id: str
    material: str
    color: str
    grams: float
    remaining_grams: float


class StockLow(Event):
    subject: ClassVar[str] = "evt.inventory.stock.low"
    material: str
    color: str
    on_hand_grams: float
    reorder_point_grams: float


class ReorderPlaced(Event):
    subject: ClassVar[str] = "evt.inventory.reorder.placed"
    po_id: str
    material: str
    color: str
    spools: int
    est_cost: float


class AvailabilityQuery(Command):
    # Sent with core NATS request/reply, never persisted.
    subject: ClassVar[str] = "qry.inventory.availability"
    kind: ClassVar[Kind] = Kind.COMMAND
    material: str
    color: str


class AvailabilityReply(Reply):
    subject: ClassVar[str] = "qry.inventory.availability.reply"
    material: str
    color: str
    on_hand_grams: float
    reserved_grams: float


# ---------------------------------------------------------------- social listening / trends (LLM agents)


class CaptureSubmitted(Command):
    """A post captured by a human group member via the browser extension."""

    subject: ClassVar[str] = "cmd.social.ingest_capture"
    capture_id: str
    group_name: str
    post_url: str
    post_text: str = Field(max_length=5000)
    captured_by: str
    captured_at: datetime


class LeadDetected(Event):
    subject: ClassVar[str] = "evt.social.lead.detected"
    lead_id: str
    capture_id: str
    intent_score: float = Field(ge=0, le=1)
    product_guess: str
    summary: str


class ReplyDrafted(Event):
    subject: ClassVar[str] = "evt.social.reply.drafted"
    lead_id: str
    draft: str
    approval_id: str             # nothing is posted until this approval is granted


class TrendSignal(Event):
    subject: ClassVar[str] = "evt.trend.signal.detected"
    signal_id: str
    topic: str
    direction: str               # "rising" | "falling"
    strength: float
    evidence: list[str]


# ---------------------------------------------------------------- platform


class DeadLettered(Event):
    subject: ClassVar[str] = "dlq.message.dead_lettered"
    original_subject: str
    original_id: str
    consumer: str
    error: str
    deliveries: int
    original: dict
