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
from printfarm.contracts.config_keys import CONFIG_KEYS
from printfarm.contracts.messages import (
    AdvanceOrder,
    CalibratePrinter,
    CancelOrder,
    CancelPrint,
    ConfirmPoOrdered,
    CreateExpense,
    CreateOrder,
    DecideApproval,
    LoadSpool,
    MarkBedCleared,
    PauseAll,
    PausePrint,
    ReceivePo,
    RecordPayment,
    RecordQc,
    RefundPayment,
    ResumePrint,
    SetConfig,
    ShipOrder,
)


@dataclass(frozen=True)
class CommandSpec:
    model: type[Payload]
    actor_field: str | None = None                       # filled with the logged-in user
    deadline_s: float | None = None                      # agents drop the command if it is older than this
    fill: Callable[[dict, str], None] | None = None      # server-side defaults: (data, command_id) -> None


def _new_order(data: dict, command_id: str) -> None:
    # Deterministic from the command id, so a retried request creates the same order, not a second one.
    data["order_id"] = f"ord-{command_id[:8]}"


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
    if type(data.get("value")) is not type(spec.default):
        raise ValueError(f"{spec.key} takes a {type(spec.default).__name__} value")


COMMANDS: dict[str, CommandSpec] = {
    "orders.create": CommandSpec(CreateOrder, "created_by", fill=_new_order),
    "orders.advance": CommandSpec(AdvanceOrder, "requested_by"),
    "orders.qc": CommandSpec(RecordQc, "inspected_by"),
    "orders.ship": CommandSpec(ShipOrder, "shipped_by"),
    "orders.cancel": CommandSpec(CancelOrder, "cancelled_by"),
    "approvals.decide": CommandSpec(DecideApproval, "decided_by"),
    "printers.pause": CommandSpec(PausePrint, "requested_by", deadline_s=30),
    "printers.resume": CommandSpec(ResumePrint, "requested_by", deadline_s=30),
    "printers.cancel": CommandSpec(CancelPrint, "requested_by", deadline_s=30),
    "printers.pause_all": CommandSpec(PauseAll, "requested_by", deadline_s=30),
    "printers.clear_bed": CommandSpec(MarkBedCleared, "operator"),
    # Calibration moves the machine: a request that waited (agent down) must not start it later.
    "printers.calibrate": CommandSpec(CalibratePrinter, "requested_by", deadline_s=30),
    "payments.record": CommandSpec(RecordPayment, "recorded_by"),
    "payments.refund": CommandSpec(RefundPayment, "recorded_by"),
    "expenses.create": CommandSpec(CreateExpense, "created_by"),
    "po.confirm_ordered": CommandSpec(ConfirmPoOrdered, "confirmed_by"),
    "po.receive": CommandSpec(ReceivePo, "received_by"),
    "spools.load": CommandSpec(LoadSpool, "loaded_by"),
    "config.set": CommandSpec(SetConfig, "changed_by", fill=_known_setting),
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
