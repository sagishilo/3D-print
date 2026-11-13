"""Typed payloads for every message on the bus.

Naming rules
------------
* Events:    ``evt.<domain>.<entity>.<past-tense-verb>``  (JetStream stream EVENTS)
* Commands:  ``cmd.<owning-agent>.<imperative>``           (JetStream stream COMMANDS, work queue)
* Telemetry: ``tlm.<printer_id>.state``                    (JetStream stream TELEMETRY, memory, 1h)
* Queries:   ``qry.<owning-agent>.<noun>``                 (core NATS request/reply, not persisted)

A breaking change to a payload means a new class with ``version = 2`` that is
published side by side with v1 until every consumer has migrated. Adding an
optional field with a default is not a breaking change.

Money is always an integer number of agorot (1/100 shekel) in fields named
``*_agorot``. Gross amounts include VAT; the Finance agent splits net and VAT.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

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


class PaymentMethod(StrEnum):
    BIT = "bit"
    PAYBOX = "paybox"
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"
    CARD = "card"
    UNKNOWN = "unknown"


class ExpenseCategory(StrEnum):
    FILAMENT = "filament"
    ELECTRICITY = "electricity"
    SHIPPING = "shipping"
    PACKAGING = "packaging"
    EQUIPMENT = "equipment"
    SOFTWARE = "software"
    MARKETING = "marketing"
    MISC = "misc"


class JobShare(BaseModel):
    """One job's share of a plate: lets Finance allocate filament and electricity per product."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    order_id: str
    product_type: str = "nameplate"
    grams: float = Field(ge=0)


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


class RecordPayment(Command):
    """A customer paid (dashboard: Finance > Record payment). No order id = income without an order, e.g. a market stall."""

    subject: ClassVar[str] = "cmd.coordinator.record_payment"
    order_id: str | None = None
    amount_agorot: int = Field(gt=0)            # gross, VAT included
    method: PaymentMethod = PaymentMethod.UNKNOWN
    paid_at: datetime | None = None
    receipt_no: str | None = Field(default=None, max_length=64)   # from the external invoicing system
    product_type: str | None = None
    recorded_by: str = ""


class RefundPayment(Command):
    subject: ClassVar[str] = "cmd.coordinator.refund_payment"
    order_id: str | None = None
    amount_agorot: int = Field(gt=0)            # gross amount given back, as a positive number
    method: PaymentMethod = PaymentMethod.UNKNOWN
    refunded_at: datetime | None = None
    receipt_no: str | None = Field(default=None, max_length=64)
    product_type: str | None = None
    recorded_by: str = ""


class PaymentRecorded(Event):
    subject: ClassVar[str] = "evt.order.payment.recorded"
    payment_id: str
    order_id: str | None = None
    amount_agorot: int                          # gross
    method: PaymentMethod
    paid_at: datetime
    receipt_no: str | None = None
    product_type: str | None = None


class PaymentRefunded(Event):
    subject: ClassVar[str] = "evt.order.payment.refunded"
    payment_id: str
    order_id: str | None = None
    amount_agorot: int                          # gross, positive; Finance books it as negative income
    method: PaymentMethod
    refunded_at: datetime
    receipt_no: str | None = None
    product_type: str | None = None


class SetConfig(Command):
    """Change an automation setting (dashboard: System > Config). Audited by the coordinator."""

    subject: ClassVar[str] = "cmd.coordinator.set_config"
    key: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*$", max_length=64)
    value: Any
    changed_by: str = ""


class ConfigChanged(Event):
    subject: ClassVar[str] = "evt.system.config.changed"
    key: str
    value: Any
    previous: Any = None
    changed_by: str = ""


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
    product_type: str = "nameplate"


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
    print_seconds: int = 0      # from print start to finish (0 if the start was never observed)
    job_shares: list[JobShare] = Field(default_factory=list)  # grams_used split by job (failed plates too)


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
    requested_by: str = ""


class ResumePrint(Command):
    subject: ClassVar[str] = "cmd.telemetry.resume_print"
    printer_id: str
    requested_by: str = ""


class CancelPrint(Command):
    subject: ClassVar[str] = "cmd.telemetry.cancel_print"
    printer_id: str
    reason: str = ""
    requested_by: str = ""


class PauseAll(Command):
    """Pause every printer that is printing (dashboard: Pause all printers)."""

    subject: ClassVar[str] = "cmd.telemetry.pause_all"
    reason: str = ""
    requested_by: str = ""


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
    """The reorder policy decided to buy (auto-approved or approved by a human). Not yet ordered from a supplier."""

    subject: ClassVar[str] = "evt.inventory.reorder.placed"
    po_id: str
    material: str
    color: str
    spools: int
    est_cost_agorot: int


class ConfirmPoOrdered(Command):
    """A human ordered the PO from the supplier; records the real total (dashboard: Filament > Purchase orders)."""

    subject: ClassVar[str] = "cmd.inventory.confirm_po_ordered"
    po_id: str
    supplier: str = Field(min_length=1, max_length=120)
    total_agorot: int = Field(gt=0)             # gross, as on the supplier's invoice
    vat_invoice: bool = True                    # False: no tax invoice, VAT can't be reclaimed
    ordered_at: datetime | None = None
    confirmed_by: str = ""


class PurchaseOrdered(Event):
    subject: ClassVar[str] = "evt.inventory.po.ordered"
    po_id: str
    material: str
    color: str
    spools: int
    supplier: str
    total_agorot: int
    vat_invoice: bool
    ordered_at: datetime


class ReceivePo(Command):
    subject: ClassVar[str] = "cmd.inventory.receive_po"
    po_id: str
    location: str = ""                          # shelf, e.g. "B2"
    received_by: str = ""


class PoReceived(Event):
    subject: ClassVar[str] = "evt.inventory.po.received"
    po_id: str
    material: str
    color: str
    spool_ids: list[str]


class LoadSpool(Command):
    """Put a spool on a printer, or back on the shelf when printer_id is empty."""

    subject: ClassVar[str] = "cmd.inventory.load_spool"
    spool_id: str
    printer_id: str | None = None
    loaded_by: str = ""


class SpoolLoaded(Event):
    subject: ClassVar[str] = "evt.inventory.spool.loaded"
    spool_id: str
    printer_id: str | None = None
    material: str
    color: str


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


# ---------------------------------------------------------------- finance (ledger)


class CreateExpense(Command):
    """A manual expense (dashboard: Finance > Add expense)."""

    subject: ClassVar[str] = "cmd.finance.create_expense"
    category: ExpenseCategory
    gross_agorot: int = Field(gt=0)
    vat_invoice: bool = False                   # True: a tax invoice exists, so VAT is split out
    estimated: bool = False                     # True: placeholder until the real bill arrives
    vendor: str | None = Field(default=None, max_length=120)
    date: datetime | None = None
    note: str | None = Field(default=None, max_length=500)
    created_by: str = ""


class LedgerEntryCreated(Event):
    """A row was added to the ledger; dashboards refresh their totals."""

    subject: ClassVar[str] = "evt.finance.entry.created"
    entry_id: int
    ledger: str                                 # "income" | "expense"
    entry_kind: str                             # income: auto | manual; expense: cash | estimated | allocated
    category: str | None = None
    gross_agorot: int
    source_event_id: str


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
