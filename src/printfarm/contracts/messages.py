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
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from printfarm.contracts.catalog import CATEGORIES, NICHE_COLORS, NICHE_PATTERN, SKU_PATTERN, UNASSIGNED, valid_niche
from printfarm.contracts.envelope import Kind, Payload
from printfarm.contracts.orders import CHANNELS, OrderLine, price_order

# ---------------------------------------------------------------- shared types


class PrinterStatus(StrEnum):
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"
    OFFLINE = "offline"
    CALIBRATING = "calibrating"   # running a calibration routine the telemetry agent started (no plate)


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
    PARTS = "parts"                 # parts, packaging stock and consumables bought for the bill of materials
    MISC = "misc"


class JobShare(BaseModel):
    """One job's share of a plate: lets Finance allocate filament and electricity per product."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    order_id: str
    product_type: str = "nameplate"
    grams: float = Field(ge=0)


class QcCheck(BaseModel):
    """One line of a niche's QC checklist as the inspector ticked it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item: str = Field(min_length=1, max_length=120)
    ok: bool


class PaymentLine(BaseModel):
    """An order line a payment pays for: Finance splits the payment across lines by ``weight_agorot``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_type: str
    niche: str = UNASSIGNED
    weight_agorot: int = Field(ge=0)        # the line total (the quantity when the order has no prices)


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


# ---------------------------------------------------------------- orders v2 (Order Pipeline; contracts/orders.py)


class CreateOrder(Command):
    """A new order with line items (dashboard: New manual order). ``as_lead``: not confirmed yet."""

    subject: ClassVar[str] = "cmd.coordinator.create_order"
    order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")   # safe in job ids and file names
    customer_name: str = Field(min_length=1, max_length=80)
    customer_ref: str = Field(default="", max_length=80)       # phone / e-mail / handle
    channel: str = "manual"
    items: list[OrderLine] = Field(min_length=1, max_length=20)
    discount_agorot: int = Field(default=0, ge=0)
    shipping_agorot: int = Field(default=0, ge=0)
    due_by: datetime | None = None
    notes: str = Field(default="", max_length=500)
    as_lead: bool = False
    dispatch: bool = False       # a stock batch (Instant Catalog): prepare and schedule at once, whatever the switches
    created_by: str = ""

    @model_validator(mode="after")
    def _priced(self) -> CreateOrder:
        if self.channel not in CHANNELS:
            raise ValueError(f"unknown channel {self.channel!r}")
        price_order(list(self.items), discount_agorot=self.discount_agorot, shipping_agorot=self.shipping_agorot)
        return self


class AdvanceOrder(Command):
    """Move an order to another stage (dashboard: drag a card). Refused moves come back as evt.order.move.rejected.
    ``texts`` replaces item texts when retrying a failed nameplate ({item_id: text})."""

    subject: ClassVar[str] = "cmd.coordinator.advance_order"
    order_id: str
    to_state: str
    texts: dict[str, str] = {}
    requested_by: str = ""


class RecordQc(Command):
    """Quality check of a printed order. A failure reprints ``failed_units`` (all units when empty)."""

    subject: ClassVar[str] = "cmd.coordinator.record_qc"
    order_id: str
    passed: bool
    failed_units: list[str] = []
    reason: str = Field(default="", max_length=300)
    checklist: list[QcCheck] = Field(default=[], max_length=30)   # the niche's checklist as ticked
    inspected_by: str = ""


class ShipOrder(Command):
    subject: ClassVar[str] = "cmd.coordinator.ship_order"
    order_id: str
    carrier: str = Field(default="", max_length=60)
    tracking: str = Field(default="", max_length=80)
    shipped_by: str = ""


class CancelOrder(Command):
    subject: ClassVar[str] = "cmd.coordinator.cancel_order"
    order_id: str
    reason: str = Field(default="", max_length=300)
    cancelled_by: str = ""


class OrderCreated(Event):
    subject: ClassVar[str] = "evt.order.order.created"
    order_id: str
    customer_name: str
    channel: str
    items: list[OrderLine]
    total_agorot: int
    units: int
    state: str


class OrderStageChanged(Event):
    subject: ClassVar[str] = "evt.order.stage.changed"
    order_id: str
    from_state: str | None
    to_state: str
    by: str                      # the operator, or "auto" when a switch or a printer moved it
    reason: str = ""


class OrderUpdated(Event):
    """An order's progress changed without a stage change (a nameplate ready, a unit printed): screens refresh."""

    subject: ClassVar[str] = "evt.order.order.updated"
    order_id: str
    state: str
    cause: str                   # the message that changed it, e.g. "personalization.model.ready"


class OrderMoveRejected(Event):
    subject: ClassVar[str] = "evt.order.move.rejected"
    order_id: str
    from_state: str
    to_state: str
    reason: str
    requested_by: str = ""


class OrderQcRecorded(Event):
    subject: ClassVar[str] = "evt.order.qc.recorded"
    order_id: str
    passed: bool
    failed_units: list[str] = []
    reason: str = ""
    checklist: list[QcCheck] = []
    by: str = ""


class ShippedLine(BaseModel):
    """One line of a shipped order: units printed for it and units taken from the shelf (Inventory deducts the
    bill of materials: parts for what was made, packaging for what left)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_type: str                           # the SKU of a catalog line, else the product
    quantity: int = Field(ge=0)
    from_stock: int = Field(default=0, ge=0)


class OrderShipped(Event):
    """Feeds receivables (Phase 7): what the customer owes for this order."""

    subject: ClassVar[str] = "evt.order.order.shipped"
    order_id: str
    total_agorot: int
    carrier: str = ""
    tracking: str = ""
    by: str = ""
    channel: str = ""                           # "stock": shipped to the shelf (a stock batch), not to a customer
    lines: list[ShippedLine] = []


class StockMoved(Event):
    """Finished goods on the shelf moved (coordinator.finished_goods): built by a stock batch, reserved for or
    released by an order, sold, or a stock count corrected."""

    subject: ClassVar[str] = "evt.order.stock.moved"
    sku: str
    delta_on_hand: int = 0
    delta_reserved: int = 0
    reason: Literal["built", "reserved", "released", "sold", "counted"]
    order_id: str | None = None
    by: str = "auto"
    note: str = ""


class SetStockRule(Command):
    """Shelf settings of a SKU (dashboard: Inventory > Finished goods): the minimum to keep and where it sits."""

    subject: ClassVar[str] = "cmd.coordinator.set_stock_rule"
    sku: str = Field(pattern=SKU_PATTERN)
    min_stock: int = Field(ge=0, le=10_000)
    location: str = Field(default="", max_length=40)
    changed_by: str = ""


class CountStock(Command):
    """A stock count: how many finished pieces are on the shelf (corrects on_hand; never below what is reserved)."""

    subject: ClassVar[str] = "cmd.coordinator.count_stock"
    sku: str = Field(pattern=SKU_PATTERN)
    on_hand: int = Field(ge=0, le=100_000)
    note: str = Field(default="", max_length=200)
    counted_by: str = ""


class StockRuleChanged(Event):
    subject: ClassVar[str] = "evt.order.stock.rule_changed"
    sku: str
    min_stock: int
    location: str
    by: str


class OrderCancelled(Event):
    subject: ClassVar[str] = "evt.order.order.cancelled"
    order_id: str
    reason: str = ""
    by: str = ""


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
    lines: list[PaymentLine] = []               # the order's lines (empty: income without an order)


class PaymentRefunded(Event):
    subject: ClassVar[str] = "evt.order.payment.refunded"
    payment_id: str
    order_id: str | None = None
    amount_agorot: int                          # gross, positive; Finance books it as negative income
    method: PaymentMethod
    refunded_at: datetime
    receipt_no: str | None = None
    product_type: str | None = None
    lines: list[PaymentLine] = []


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
    item_id: str = ""            # orders v2: one nameplate per order line ("" = a v1 single-item order)
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
    item_id: str = ""
    artifact_uri: str            # where the printable file lives
    source_uri: str              # parametric source (.scad) for re-generation
    footprint_mm: tuple[float, float]
    est_grams: float
    est_minutes: float
    sliced: bool                 # False => estimates only, slicer CLI not installed


class ModelFailed(Event):
    subject: ClassVar[str] = "evt.personalization.model.failed"
    order_id: str
    item_id: str = ""
    reason: str


# ---------------------------------------------------------------- instant catalog (personalization owns catalog.*)


class UpsertCatalogProduct(Command):
    """Add a SKU or change one (dashboard: Import 3MF, Edit). ``upload`` is the SHA-256 of a 3MF the gateway put into
    the uploads object store; empty keeps the stored template. A sliced template's time and weight come from the file;
    an unsliced one needs ``est_grams`` and ``est_minutes``."""

    subject: ClassVar[str] = "cmd.personalization.upsert_product"
    sku: str = Field(pattern=SKU_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    category: str = "other"
    material: str = Field(default="PLA", min_length=1, max_length=20)
    color: str = Field(default="black", min_length=1, max_length=20)
    price_agorot: int = Field(ge=0, le=10_000_000)                 # gross, as sold
    upload: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")
    upload_name: str = Field(default="", max_length=120)
    est_grams: float | None = Field(default=None, gt=0, le=5000)
    est_minutes: float | None = Field(default=None, gt=0, le=10_000)
    active: bool = True
    niche: str | None = Field(default=None, pattern=NICHE_PATTERN)   # None: keep (a new SKU: unassigned)
    post_minutes: float | None = Field(default=None, ge=0, le=600)   # post-processing labour per piece; None: keep
    tags: list[str] | None = Field(default=None, max_length=8)      # extra niches; None: keep
    changed_by: str = ""

    @model_validator(mode="after")
    def _category(self) -> UpsertCatalogProduct:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}")
        if self.tags and any(not valid_niche(t) for t in self.tags):
            raise ValueError("each tag is a niche slug")
        return self


class UpsertNiche(Command):
    """Add a niche or change one (dashboard: Niches, owner only): its colour and the defaults its products start with."""

    subject: ClassVar[str] = "cmd.personalization.upsert_niche"
    slug: str = Field(pattern=NICHE_PATTERN)
    name: str = Field(min_length=1, max_length=40)
    color: int = Field(default=1, ge=1, le=NICHE_COLORS)
    icon: str = Field(default="tag", max_length=20)
    sort: int = Field(default=100, ge=0, le=999)
    default_material: str = Field(default="PLA", min_length=1, max_length=20)
    default_colors: list[str] = Field(default=[], max_length=12)
    target_margin_pct: float = Field(default=50, ge=0, lt=100)
    qc_checklist: list[str] = Field(default=[], max_length=20)
    packaging: list[str] = Field(default=[], max_length=12)
    changed_by: str = ""

    @model_validator(mode="after")
    def _clean(self) -> UpsertNiche:
        if any(not c.strip() or len(c) > 120 for c in self.qc_checklist):
            raise ValueError("each checklist line needs 1-120 characters")
        if any(not c.strip() or len(c) > 40 for c in [*self.default_colors, *self.packaging]):
            raise ValueError("colours and packaging items need 1-40 characters each")
        return self


class ArchiveNiche(Command):
    """Hide a niche from pickers (its history stays); its products move to ``move_to``. ``archived=False`` restores."""

    subject: ClassVar[str] = "cmd.personalization.archive_niche"
    slug: str = Field(pattern=NICHE_PATTERN)
    archived: bool = True
    move_to: str = Field(default=UNASSIGNED, pattern=NICHE_PATTERN)
    changed_by: str = ""


class AssignNiche(Command):
    """Bulk: set the main niche of several SKUs (dashboard: Catalog > select > Assign niche)."""

    subject: ClassVar[str] = "cmd.personalization.assign_niche"
    skus: list[str] = Field(min_length=1, max_length=200)
    niche: str = Field(pattern=NICHE_PATTERN)
    changed_by: str = ""


class NicheUpdated(Event):
    subject: ClassVar[str] = "evt.catalog.niche.updated"
    slug: str
    name: str
    archived: bool
    by: str
    skus_moved: int = 0


class NicheRejected(Event):
    subject: ClassVar[str] = "evt.catalog.niche.rejected"
    slug: str
    reason: str
    by: str


class SliceCatalogProduct(Command):
    """Slice a SKU's unsliced template with the slicer CLI on the personalization agent's host (when installed)."""

    subject: ClassVar[str] = "cmd.personalization.slice_product"
    sku: str = Field(pattern=SKU_PATTERN)
    requested_by: str = ""


class PrepareCatalogItem(Command):
    """An order line of a catalog SKU: its template becomes the line's model (ModelReady, or ModelFailed)."""

    subject: ClassVar[str] = "cmd.personalization.prepare_catalog_item"
    order_id: str
    item_id: str
    sku: str


class CatalogProductUpdated(Event):
    subject: ClassVar[str] = "evt.catalog.product.updated"
    sku: str
    name: str
    active: bool
    sliced: bool
    by: str


class CatalogProductRejected(Event):
    """An upsert or a slice that did not happen; ``action`` is "upsert" or "slice"."""

    subject: ClassVar[str] = "evt.catalog.product.rejected"
    sku: str
    action: str
    reason: str
    by: str


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
    whole_plate: bool = False    # a sliced file: printed alone, as the slicer arranged its plate


class CancelOrderJobs(Command):
    """Drop an order's jobs that are still queued (cancelled order). Jobs already on a plate are left alone."""

    subject: ClassVar[str] = "cmd.fleet.cancel_jobs"
    order_id: str


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
    printer_watts: float | None = None   # the printer's configured power draw; None = the finance default


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


class AmsTray(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    unit: int
    slot: int
    material: str | None = None
    color: str | None = None              # "#RRGGBB"
    remaining_pct: int | None = None      # -1 / unknown -> None


class AmsUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    unit: int
    humidity_level: int | None = None     # Bambu's 1-5 humidity index, as the printer reports it
    temp: float | None = None
    trays: list[AmsTray] = []


class PrinterHardware(BaseModel):
    """Deeper hardware readings for the dashboard; every field is optional (vendors report different things)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    chamber_temp: float | None = None
    fans: dict[str, int] = {}             # percent per fan: part, aux, chamber, hotend
    speed_mode: str | None = None         # Bambu: silent | standard | sport | ludicrous
    speed_pct: int | None = None
    nozzle_diameter: float | None = None
    nozzle_type: str | None = None
    wifi_dbm: int | None = None
    firmware: str | None = None
    stage: str | None = None              # what the printer is doing right now ("auto bed leveling", ...)
    homed_axes: str | None = None         # Klipper: "xyz" when homed
    bed_mesh: str | None = None           # Klipper: active bed mesh profile
    host_load: float | None = None        # Klipper host: 1-minute load average
    ams: list[AmsUnit] = []
    hms: list[str] = []                   # Bambu health codes, e.g. "0300_0100_0001_0007"


class CalibratePrinter(Command):
    """Run one calibration routine (contracts/calibration.py). Refused unless the printer is online, idle and the
    operator confirmed the bed is empty; the outcome is always published as evt.printer.calibration.finished."""

    subject: ClassVar[str] = "cmd.telemetry.calibrate"
    printer_id: str
    routine: str = Field(pattern=r"^[a-z_]{2,32}$")
    bed_clear_confirmed: bool = False
    temperature: int | None = Field(default=None, ge=40, le=300)   # PID routines only
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
    hardware: PrinterHardware | None = None


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
    bed_state_unknown: bool = False   # the end was not seen (lost print): never assume the bed is empty


class FailureDetected(Event):
    subject: ClassVar[str] = "evt.printer.failure.detected"
    printer_id: str
    plate_id: str | None
    detector: str
    severity: Severity
    message: str
    action_taken: str            # "pause_sent" | "none" | "pause_failed: ..."; the result follows as pause.confirmed/unconfirmed


class PlateUploaded(Event):
    """The plate file reached the printer (Bambu: implicit FTPS; Moonraker: HTTP upload)."""

    subject: ClassVar[str] = "evt.printer.plate.uploaded"
    printer_id: str
    plate_id: str
    remote_file: str
    bytes: int
    md5: str
    seconds: float


class PauseConfirmed(Event):
    """The printer itself reported that it stopped printing after a pause request."""

    subject: ClassVar[str] = "evt.printer.pause.confirmed"
    printer_id: str
    plate_id: str | None = None
    reason: str
    requested_by: str
    latency_ms: int              # pause sent -> printer reported a non-printing state
    attempts: int
    status: PrinterStatus        # normally "paused"; "failed"/"finished"/"idle" if it stopped another way


class PauseUnconfirmed(Event):
    """SAFETY ALERT: a pause was requested but the printer still reports printing (or cannot be reached)."""

    subject: ClassVar[str] = "evt.printer.pause.unconfirmed"
    printer_id: str
    plate_id: str | None = None
    reason: str
    requested_by: str
    waited_ms: int
    attempts: int
    last_status: PrinterStatus | None = None
    online: bool
    printer_reply: str = ""      # what the printer answered to the pause command, if it answered


class CalibrationStarted(Event):
    subject: ClassVar[str] = "evt.printer.calibration.started"
    printer_id: str
    calibration_id: str
    routine: str
    requested_by: str


class CalibrationFinished(Event):
    """Every calibration request ends here: done, failed, or refused before it started (``started`` False)."""

    subject: ClassVar[str] = "evt.printer.calibration.finished"
    printer_id: str
    calibration_id: str
    routine: str
    succeeded: bool
    started: bool = True
    detail: str = ""
    seconds: float = 0.0


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
    item_id: str | None = None                  # a part, packaging or consumable (inventory.items), not filament
    quantity: float | None = None


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
    grams_per_spool: float | None = None        # lets Finance price filament per gram from what was really paid
    item_id: str | None = None                  # an item PO: booked as packaging or parts, not filament
    item_kind: str | None = None                # part | packaging | consumable
    quantity: float | None = None


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
    item_id: str | None = None
    quantity: float | None = None


class LoadSpool(Command):
    """Put a spool on a printer (``slot``: AMS slot / tool), or back on the shelf (``location``) when printer_id is
    empty."""

    subject: ClassVar[str] = "cmd.inventory.load_spool"
    spool_id: str
    printer_id: str | None = None
    slot: str = Field(default="", max_length=40)
    location: str = Field(default="", max_length=40)
    loaded_by: str = ""


class RegisterSpool(Command):
    """A spool that did not come through a purchase order (gift, market, first stock-take)."""

    subject: ClassVar[str] = "cmd.inventory.register_spool"
    spool_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    material: str = Field(min_length=1, max_length=20)
    color: str = Field(min_length=1, max_length=30)
    brand: str = Field(default="", max_length=40)
    initial_grams: float = Field(gt=0, le=10_000)
    remaining_grams: float | None = Field(default=None, ge=0, le=10_000)    # None = a full spool
    location: str = Field(default="", max_length=40)
    tag: str = Field(default="", max_length=80)                              # QR label / RFID id
    registered_by: str = ""


class AdjustSpool(Command):
    """Correct a spool's remaining filament after weighing it (net grams), or mark it empty."""

    subject: ClassVar[str] = "cmd.inventory.adjust_spool"
    spool_id: str
    remaining_grams: float = Field(ge=0, le=10_000)
    reason: str = Field(default="weighed", max_length=120)
    adjusted_by: str = ""


class SetReorderRule(Command):
    """Dashboard: Filament > Reorder rules. Fields left out keep their value."""

    subject: ClassVar[str] = "cmd.inventory.set_reorder_rule"
    material: str
    color: str
    min_stock_grams: float | None = Field(default=None, ge=0, le=1_000_000)
    mode: str | None = Field(default=None, pattern=r"^(auto|manual)$")
    supplier: str | None = Field(default=None, max_length=120)
    lead_time_days: float | None = Field(default=None, ge=0, le=120)
    changed_by: str = ""


class RequestReorder(Command):
    """A person orders now ("Order now" in the reorder list). spools = None: what the rule suggests."""

    subject: ClassVar[str] = "cmd.inventory.request_reorder"
    material: str
    color: str
    spools: int | None = Field(default=None, ge=1, le=100)
    requested_by: str = ""


class UpdatePo(Command):
    """Change the quantity of a purchase order still waiting for approval."""

    subject: ClassVar[str] = "cmd.inventory.update_po"
    po_id: str
    spools: int = Field(ge=1, le=100)
    updated_by: str = ""


class AcknowledgeInventoryAlert(Command):
    subject: ClassVar[str] = "cmd.inventory.ack_alert"
    alert_id: str
    acknowledged_by: str = ""


# ---------------------------------------------------------------- parts, packaging, consumables (Wave 1 / M4)

ITEM_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"


class UpsertItem(Command):
    """Add a stock item besides filament (magnets, inserts, screws, boxes, labels) or change one. ``on_hand`` only
    for a new item: later changes are counts (CountItem) or deliveries (purchase orders)."""

    subject: ClassVar[str] = "cmd.inventory.upsert_item"
    item_id: str = Field(pattern=ITEM_ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    item_kind: Literal["part", "packaging", "consumable"] = "part"
    unit: str = Field(default="pcs", min_length=1, max_length=12)
    on_hand: float | None = Field(default=None, ge=0, le=1_000_000)
    min_qty: float = Field(default=0, ge=0, le=1_000_000)
    reorder_qty: float = Field(default=0, ge=0, le=1_000_000)
    supplier: str = Field(default="", max_length=120)
    lead_days: int = Field(default=7, ge=0, le=365)
    cost_agorot: int = Field(default=0, ge=0, le=10_000_000)     # net price of one unit
    archived: bool = False
    changed_by: str = ""


class CountItem(Command):
    subject: ClassVar[str] = "cmd.inventory.count_item"
    item_id: str = Field(pattern=ITEM_ID_PATTERN)
    on_hand: float = Field(ge=0, le=1_000_000)
    counted_by: str = ""


class BomLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    item_id: str = Field(pattern=ITEM_ID_PATTERN)
    qty: float = Field(gt=0, le=1000)


class SetBom(Command):
    """What one finished piece of a SKU takes besides filament (replaces the SKU's whole list)."""

    subject: ClassVar[str] = "cmd.inventory.set_bom"
    sku: str = Field(pattern=SKU_PATTERN)
    lines: list[BomLine] = Field(default=[], max_length=20)
    changed_by: str = ""


class ReorderItem(Command):
    """Order an item now (a person decided: no approval). quantity = None: the item's reorder quantity."""

    subject: ClassVar[str] = "cmd.inventory.reorder_item"
    item_id: str = Field(pattern=ITEM_ID_PATTERN)
    quantity: float | None = Field(default=None, gt=0, le=1_000_000)
    requested_by: str = ""


class ItemChanged(Event):
    """An item was added, changed, counted, used (BOM) or received."""

    subject: ClassVar[str] = "evt.inventory.item.changed"
    item_id: str
    name: str
    on_hand: float
    delta: float = 0
    reason: str                                  # saved | counted | made | shipped | received
    by: str = "auto"


class BomChanged(Event):
    subject: ClassVar[str] = "evt.inventory.bom.changed"
    sku: str
    lines: list[BomLine]
    by: str


class MarkSpoolDried(Command):
    """The spool came out of the dryer (filament condition: the drying clock starts again)."""

    subject: ClassVar[str] = "cmd.inventory.mark_dried"
    spool_id: str
    dried_by: str = ""


class SpoolDried(Event):
    subject: ClassVar[str] = "evt.inventory.spool.dried"
    spool_id: str
    material: str
    color: str
    by: str


class SpoolRegistered(Event):
    subject: ClassVar[str] = "evt.inventory.spool.registered"
    spool_id: str
    material: str
    color: str
    brand: str
    remaining_grams: float


class SpoolAdjusted(Event):
    subject: ClassVar[str] = "evt.inventory.spool.adjusted"
    spool_id: str
    material: str
    color: str
    delta_grams: float
    remaining_grams: float
    reason: str
    by: str


class ReorderRuleChanged(Event):
    subject: ClassVar[str] = "evt.inventory.rule.changed"
    material: str
    color: str
    min_stock_grams: float
    mode: str
    supplier: str
    lead_time_days: float
    by: str


class InventoryAlertRaised(Event):
    """Below minimum stock, a loaded spool too short for a scheduled plate, or a spool run empty."""

    subject: ClassVar[str] = "evt.inventory.alert.raised"
    alert_id: str
    alert_kind: str              # low_stock | spool_short | spool_empty ("kind" is the envelope's)
    severity: str
    material: str
    color: str
    message: str
    spool_id: str | None = None
    printer_id: str | None = None
    plate_id: str | None = None
    needed_grams: float | None = None
    available_grams: float | None = None


class InventoryAlertResolved(Event):
    subject: ClassVar[str] = "evt.inventory.alert.resolved"
    alert_id: str
    alert_kind: str
    by: str                      # "auto" when the condition cleared


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


class UpdateFinanceSettings(Command):
    """Finance > Settings. ``confirm``: the values were checked with the accountant (until then they are placeholders)."""

    subject: ClassVar[str] = "cmd.finance.update_settings"
    business_type: str = Field(pattern=r"^(murshe|patur)$")
    vat_rate: float = Field(ge=0, le=0.5)
    electricity_agorot_per_kwh: float = Field(gt=0, le=1000)
    default_printer_watts: float = Field(gt=0, le=5000)
    default_filament_agorot_per_gram: float = Field(gt=0, le=1000)
    turnover_ceiling_agorot: int | None = Field(default=None, ge=0)
    # Wave 1 / M5 (None: keep): post-processing labour per hour, and the % of the gross price each channel keeps
    labour_agorot_per_hour: float | None = Field(default=None, ge=0, le=100_000)
    channel_fees: dict[str, float] | None = None
    confirm: bool = False
    updated_by: str = ""

    @model_validator(mode="after")
    def _fees(self) -> UpdateFinanceSettings:
        for channel, pct in (self.channel_fees or {}).items():
            if channel not in CHANNELS:
                raise ValueError(f"unknown channel {channel!r}")
            if not 0 <= pct <= 50:
                raise ValueError(f"the {channel} fee is a percentage from 0 to 50")
        return self


class FinanceSettingsChanged(Event):
    subject: ClassVar[str] = "evt.finance.settings.changed"
    business_type: str
    vat_rate: float
    confirmed: bool
    by: str


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
    """A post captured by a human group member via the browser extension (ADR-0007: never scraped)."""

    subject: ClassVar[str] = "cmd.social.ingest_capture"
    capture_id: str
    group_name: str = Field(max_length=200)
    post_url: str = Field(max_length=1000)
    post_text: str = Field(min_length=1, max_length=5000)
    captured_by: str = Field(max_length=200)
    captured_at: datetime


class CaptureScored(Event):
    """What became of a capture: ignored by the prefilter, scored by the model, a lead, a duplicate, waiting
    (no API key, budget used up, switched off) or failed."""

    subject: ClassVar[str] = "evt.social.capture.scored"
    capture_id: str
    group_name: str
    status: str
    reason: str = ""
    prefilter: float | None = None
    intent: str | None = None
    intent_score: float | None = None
    sku_guess: str | None = None
    summary: str = ""


class LeadDetected(Event):
    subject: ClassVar[str] = "evt.social.lead.detected"
    lead_id: str
    capture_id: str
    intent_score: float = Field(ge=0, le=1)
    product_guess: str
    summary: str


class ReplyDrafted(Event):
    """The model's draft is waiting for a person: nothing is ever posted by the system."""

    subject: ClassVar[str] = "evt.social.reply.drafted"
    lead_id: str
    capture_id: str
    draft: str
    sku: str | None = None
    failed: str = ""             # the draft could not be written (the lead is still shown, without a draft)


class DecideLead(Command):
    """Approve (with the text the person will post, edited or not) or dismiss a lead's reply."""

    subject: ClassVar[str] = "cmd.social.decide_lead"
    lead_id: str
    approve: bool
    final_text: str = Field(default="", max_length=2000)
    decided_by: str


class MarkLeadPosted(Command):
    """The person posted the approved reply themselves (the system never posts)."""

    subject: ClassVar[str] = "cmd.social.mark_posted"
    lead_id: str
    posted_by: str


class LabelCapture(Command):
    """A person's verdict on a capture; it joins the labelled set the prefilter and the accuracy use."""

    subject: ClassVar[str] = "cmd.social.label_capture"
    capture_id: str
    label: Literal["buyer", "not_buyer"]
    labelled_by: str


class RescoreCapture(Command):
    """Score a capture again (after it waited for a key or the budget, or after a failure)."""

    subject: ClassVar[str] = "cmd.social.rescore"
    capture_id: str
    requested_by: str


class LeadChanged(Event):
    subject: ClassVar[str] = "evt.social.lead.changed"
    lead_id: str
    capture_id: str
    status: str
    by: str
    final_text: str = ""


class CaptureLabelled(Event):
    subject: ClassVar[str] = "evt.social.capture.labelled"
    capture_id: str
    label: str
    by: str


class TrendSignal(Event):
    subject: ClassVar[str] = "evt.trend.signal.detected"
    signal_id: str
    topic: str                   # "niche:<slug>" | "sku:<SKU>"
    direction: str               # "rising" | "falling"
    strength: float
    evidence: list[str]
    day: str | None = None       # the business day (ISO) of the analytics it came from
    niche: str | None = None
    change_pct: float | None = None


class BackfillAnalytics(Command):
    """Rebuild the daily analytics of the last ``days`` days, then the day's signals (dashboard: Scorecard, owner)."""

    subject: ClassVar[str] = "cmd.trend.backfill"
    days: int = Field(default=30, ge=1, le=400)
    requested_by: str = ""


class AnalyticsRolledUp(Event):
    subject: ClassVar[str] = "evt.trend.rollup.done"
    days: int
    first_day: str
    last_day: str
    rows: int
    signals: int
    insights: int = 0
    by: str = "auto"


# ---------------------------------------------------------------- platform


class DeadLettered(Event):
    subject: ClassVar[str] = "dlq.message.dead_lettered"
    original_subject: str
    original_id: str
    consumer: str
    error: str
    deliveries: int
    original: dict
