"""Dashboard command catalog and WebSocket topic rules (pure; unit-tested in tests/test_gateway.py).

The browser calls stable API names (``POST /api/v1/commands/payments.record``). Each name maps to one typed contract
from ``printfarm.contracts``; the body is validated against it before anything reaches the bus. Fields that say who
did something (``decided_by``, ``recorded_by``, ...) always come from the login session, never from the client.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from printfarm.contracts import Envelope, Payload, new_message
from printfarm.contracts.catalog import STOCK_CHANNEL
from printfarm.contracts.config_keys import CONFIG_KEYS
from printfarm.contracts.messages import (
    AcknowledgeInventoryAlert,
    AdjustSpool,
    AdvanceOrder,
    CalibratePrinter,
    CancelOrder,
    CancelPrint,
    CaptureSubmitted,
    ConfirmPoOrdered,
    CreateExpense,
    CreateOrder,
    DecideApproval,
    DecideLead,
    LabelCapture,
    LoadSpool,
    MarkBedCleared,
    MarkLeadPosted,
    PauseAll,
    PausePrint,
    ReceivePo,
    RecordPayment,
    RecordQc,
    RefundPayment,
    RegisterSpool,
    RequestReorder,
    RescoreCapture,
    ResumePrint,
    SetConfig,
    SetReorderRule,
    ShipOrder,
    SliceCatalogProduct,
    UpdateFinanceSettings,
    UpdatePo,
    UpsertCatalogProduct,
)


@dataclass(frozen=True)
class CommandSpec:
    model: type[Payload]
    actor_field: str | None = None                       # filled with the logged-in user
    deadline_s: float | None = None                      # agents drop the command if it is older than this
    fill: Callable[[dict, str], None] | None = None      # server-side defaults: (data, command_id) -> None
    min_role: str = "operator"                           # viewer < operator < owner (auth.ROLE_RANK)


def _new_spool(data: dict, command_id: str) -> None:
    # A scanned label is the spool's id; otherwise one derived from the command id (a retry registers it once).
    tag = str(data.get("tag") or "").strip()
    data.setdefault("spool_id", tag if tag and all(c.isalnum() or c in "_.-" for c in tag) and len(tag) <= 40
                    else f"SP-{command_id[:8].upper()}")


def _new_order(data: dict, command_id: str) -> None:
    # Deterministic from the command id, so a retried request creates the same order, not a second one.
    data["order_id"] = f"ord-{command_id[:8]}"
    data["dispatch"] = False          # only a stock batch skips the gates


def _new_batch(data: dict, command_id: str) -> None:
    # A batch for the shelf: an order on the stock channel, no price, prepared and scheduled at once.
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("a batch needs at least one SKU")
    if not all(isinstance(i, dict) and i.get("product") == "catalog" for i in items):
        raise ValueError("a batch holds catalog SKUs only")
    data.update(order_id=f"stk-{command_id[:8]}", channel=STOCK_CHANNEL, customer_name="Stock batch", dispatch=True,
                as_lead=False, discount_agorot=0, shipping_agorot=0,
                items=[{**i, "unit_price_agorot": 0} for i in items])


def _new_capture(data: dict, command_id: str) -> None:
    # A post pasted on the Social screen, or sent by the capture extension (gateway/app/main.py). The id comes from
    # the command id, so a retried request is one capture; the agent also drops the same text captured twice.
    data["capture_id"] = f"cap-{command_id[:12]}"
    data.setdefault("captured_at", datetime.now(UTC).isoformat())
    data.setdefault("group_name", "")
    data.setdefault("post_url", "")


_SETTINGS = {k.key: k for k in CONFIG_KEYS}


def _known_setting(data: dict, command_id: str) -> None:
    # Only the dashboard's own settings, with a value of the default's type, and none that arrives in a later phase:
    # the config bucket never holds keys or values no agent understands.
    key = data.get("key")
    spec = _SETTINGS.get(key) if isinstance(key, str) else None
    if spec is None:
        raise ValueError(f"unknown setting {key!r}")
    if spec.phase is not None:
        raise ValueError(f"{spec.key} arrives in phase {spec.phase}")
    value = data.get("value")
    if isinstance(spec.default, float) and type(value) is int:
        value = data["value"] = float(value)                 # JSON has one number type: 1 means 1.0 here
    if type(value) is not type(spec.default):
        raise ValueError(f"{spec.key} takes a {type(spec.default).__name__} value")
    if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{spec.key} must be between {spec.minimum:g} and {spec.maximum:g}")


COMMANDS: dict[str, CommandSpec] = {
    "orders.create": CommandSpec(CreateOrder, "created_by", fill=_new_order),
    "orders.advance": CommandSpec(AdvanceOrder, "requested_by"),
    "orders.qc": CommandSpec(RecordQc, "inspected_by"),
    "orders.ship": CommandSpec(ShipOrder, "shipped_by"),
    "orders.cancel": CommandSpec(CancelOrder, "cancelled_by"),
    "catalog.upsert": CommandSpec(UpsertCatalogProduct, "changed_by"),
    "catalog.slice": CommandSpec(SliceCatalogProduct, "requested_by"),
    "catalog.batch": CommandSpec(CreateOrder, "created_by", fill=_new_batch),
    "approvals.decide": CommandSpec(DecideApproval, "decided_by"),
    "printers.pause": CommandSpec(PausePrint, "requested_by", deadline_s=30),
    "printers.resume": CommandSpec(ResumePrint, "requested_by", deadline_s=30),
    "printers.cancel": CommandSpec(CancelPrint, "requested_by", deadline_s=30),
    "printers.pause_all": CommandSpec(PauseAll, "requested_by", deadline_s=30),
    "printers.clear_bed": CommandSpec(MarkBedCleared, "operator"),
    # Calibration moves the machine: a request that waited (agent down) must not start it later.
    "printers.calibrate": CommandSpec(CalibratePrinter, "requested_by", deadline_s=30),
    "payments.record": CommandSpec(RecordPayment, "recorded_by"),
    "payments.refund": CommandSpec(RefundPayment, "recorded_by", min_role="owner"),
    "expenses.create": CommandSpec(CreateExpense, "created_by"),
    "po.confirm_ordered": CommandSpec(ConfirmPoOrdered, "confirmed_by"),
    "po.receive": CommandSpec(ReceivePo, "received_by"),
    "spools.load": CommandSpec(LoadSpool, "loaded_by"),
    "spools.register": CommandSpec(RegisterSpool, "registered_by", fill=_new_spool),
    "finance.settings": CommandSpec(UpdateFinanceSettings, "updated_by", min_role="owner"),
    "spools.adjust": CommandSpec(AdjustSpool, "adjusted_by"),
    "inventory.rule": CommandSpec(SetReorderRule, "changed_by"),
    "inventory.reorder": CommandSpec(RequestReorder, "requested_by"),
    "inventory.ack_alert": CommandSpec(AcknowledgeInventoryAlert, "acknowledged_by"),
    "po.update": CommandSpec(UpdatePo, "updated_by"),
    "config.set": CommandSpec(SetConfig, "changed_by", fill=_known_setting, min_role="owner"),
    # Social Listening (ADR-0007): a person captures, approves and posts; nothing here posts anything.
    "social.capture": CommandSpec(CaptureSubmitted, "captured_by", fill=_new_capture),
    "social.decide": CommandSpec(DecideLead, "decided_by"),
    "social.posted": CommandSpec(MarkLeadPosted, "posted_by"),
    "social.label": CommandSpec(LabelCapture, "labelled_by"),
    "social.rescore": CommandSpec(RescoreCapture, "requested_by"),
}

# POST /api/v1/commands/printers/{printer_id}/{action}
PRINTER_ACTIONS = {"pause": "printers.pause", "resume": "printers.resume", "cancel": "printers.cancel"}

PRINTER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def command_id_from(header: str | None) -> str:
    """The client's Idempotency-Key becomes the envelope id: JetStream and the agent inbox then drop retries."""
    if header is None or header == "":
        return uuid.uuid4().hex
    if not _IDEMPOTENCY_KEY.match(header):
        raise ValueError("Idempotency-Key must be 8-64 characters from A-Z a-z 0-9 _ -")
    return header


def build_command(name: str, body: dict, actor: str, command_id: str, now: datetime | None = None) -> Envelope:
    """Validate ``body`` for command ``name`` and wrap it in an envelope. Raises KeyError / pydantic.ValidationError."""
    spec = COMMANDS[name]
    data = dict(body)
    if spec.fill:
        spec.fill(data, command_id)
    if spec.actor_field:
        data[spec.actor_field] = actor
    payload = spec.model.model_validate(data)
    deadline = (now or datetime.now(UTC)) + timedelta(seconds=spec.deadline_s) if spec.deadline_s else None
    correlation = getattr(payload, "order_id", None) or getattr(payload, "po_id", None) or command_id
    entity = getattr(payload, "printer_id", None) or getattr(payload, "order_id", None) or getattr(payload, "po_id", None)
    return new_message(payload, source="/gateway", id=command_id, idempotencykey=command_id,
                       correlationid=correlation, subject=entity, deadline=deadline)


# ---------------------------------------------------------------- WebSocket topics

# Browsers may watch events, telemetry and dead letters. Commands (cmd.*) and queries (qry.*) live under other
# root tokens, so no pattern a browser can send ("evt.>", "tlm.*.state", ...) can ever match one.
WS_ROOTS = frozenset({"evt", "tlm", "dlq"})
WS_MAX_TOPICS = 20
_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")


def ws_topic_allowed(topic: object) -> bool:
    if not isinstance(topic, str) or not 0 < len(topic) <= 200:
        return False
    tokens = topic.split(".")
    if len(tokens) < 2 or tokens[0] not in WS_ROOTS:
        return False
    last = len(tokens) - 1
    return all(t == "*" or (t == ">" and i == last) or bool(_TOKEN.match(t)) for i, t in enumerate(tokens[1:], start=1))
