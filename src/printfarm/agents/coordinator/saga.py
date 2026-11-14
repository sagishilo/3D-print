"""Order saga v2 as a pure state machine: (state, context, message, switches) -> (new state, messages to send).

No I/O here, so every transition is unit-testable. The coordinator agent loads the saga row, calls ``decide`` and
persists the result together with the outgoing commands in one transaction.

An order has line items; each item has ``quantity`` units and each unit is printed as its own job
(``<order>-i<item>-u<unit>-a<attempt>``). Stages, as on the dashboard's board (contracts/orders.py):

    LEAD --AdvanceOrder--> RECEIVED --AdvanceOrder | auto--> PERSONALIZING (GenerateNameplate per item)
    PERSONALIZING --all ModelReady, then AdvanceOrder | auto--> SCHEDULING (EnqueueJob per unit)
    SCHEDULING --JobStarted--> PRINTING --all units printed--> QC (auto switch: passes at once)
    QC --RecordQc(pass), then ShipOrder--> SHIPPED          QC --RecordQc(fail)--> SCHEDULING (reprint units)
    ModelFailed | a unit failing MAX_PRINT_ATTEMPTS times | MAX_QC_ROUNDS QC failures --> NEEDS_ATTENTION
    NEEDS_ATTENTION --AdvanceOrder--> PERSONALIZING (retry models, texts may change) | SCHEDULING (retry units)
    open stage without a unit on a printer --CancelOrder--> CANCELLED

A move an operator may not make (e.g. into PRINTING: printers drive that) produces OrderMoveRejected and changes
nothing. ``Switches`` are the automation settings orders.auto_* (off by default: every gate waits for a person).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from printfarm.contracts import Payload
from printfarm.contracts.messages import (
    AdvanceOrder,
    CancelOrder,
    CancelOrderJobs,
    CreateOrder,
    EnqueueJob,
    GenerateNameplate,
    JobCompleted,
    JobFailed,
    JobStarted,
    ModelFailed,
    ModelReady,
    OrderCancelled,
    OrderCompleted,
    OrderMoveRejected,
    OrderNeedsAttention,
    OrderQcRecorded,
    OrderReceived,
    OrderShipped,
    RecordQc,
    ShipOrder,
)
from printfarm.contracts.orders import OrderLine, price_order

MAX_PRINT_ATTEMPTS = 2      # per unit, between two operator retries
MAX_QC_ROUNDS = 3           # failed QC checks of one order before a person must look


class State(StrEnum):
    LEAD = "LEAD"
    RECEIVED = "RECEIVED"
    PERSONALIZING = "PERSONALIZING"
    SCHEDULING = "SCHEDULING"
    PRINTING = "PRINTING"
    QC = "QC"
    SHIPPED = "SHIPPED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    CANCELLED = "CANCELLED"


TERMINAL = {State.SHIPPED, State.CANCELLED}
ON_PRINTER = {"scheduled", "printing"}          # unit statuses that mean a printer has (or is about to have) it


@dataclass(frozen=True)
class Switches:
    personalize: bool = False
    schedule: bool = False
    qc_pass: bool = False


ALL_AUTO = Switches(True, True, True)


@dataclass
class Decision:
    state: State
    context: dict[str, Any]
    outgoing: list[Payload] = field(default_factory=list)
    changed: bool = True
    by: str = "auto"            # who moved the order (for evt.order.stage.changed)
    reason: str = ""


def job_id(order_id: str, item: str, unit: str, attempt: int) -> str:
    return f"{order_id}-i{item}-u{unit}-a{attempt}"


# ---------------------------------------------------------------- start


def _context(order: dict, lines: list[OrderLine]) -> dict:
    items, units = {}, {}
    for i, line in enumerate(lines, start=1):
        items[str(i)] = {**line.model_dump(mode="json"), "unit_price": line.unit_price, "model": None,
                         "model_status": "pending", "model_error": None}
        for u in range(1, line.quantity + 1):
            units[f"{i}.{u}"] = {"attempt": 1, "tries": 0, "status": "pending"}
    return {"order": order, "items": items, "units": units, "qc": {"status": "pending", "rounds": 0}}


def start_created(cmd: CreateOrder, sw: Switches) -> Decision:
    pricing = price_order(list(cmd.items), discount_agorot=cmd.discount_agorot, shipping_agorot=cmd.shipping_agorot)
    order = {"order_id": cmd.order_id, "customer_name": cmd.customer_name, "channel": cmd.channel,
             "due_by": cmd.due_by.isoformat() if cmd.due_by else None, "total_agorot": pricing.total}
    ctx = _context(order, list(cmd.items))
    if cmd.as_lead:
        return Decision(State.LEAD, ctx, by=cmd.created_by or "unknown")
    return _received(ctx, sw, by=cmd.created_by or "unknown")


def start_legacy(msg: OrderReceived, sw: Switches) -> Decision:
    """A v1 order (CLI, automated channels): one item, price unknown (0)."""
    line = OrderLine(product=msg.product if msg.product in ("nameplate",) else "nameplate", text=msg.text,
                     material=msg.material, color=msg.color, quantity=msg.quantity, unit_price_agorot=0)
    order = {"order_id": msg.order_id, "customer_name": msg.customer_ref, "channel": "api",
             "due_by": msg.due_by.isoformat() if msg.due_by else None, "total_agorot": 0}
    return _received(_context(order, [line]), sw, by="auto")


def _received(ctx: dict, sw: Switches, by: str) -> Decision:
    if sw.personalize:
        return _personalize(State.RECEIVED, ctx, by)
    return Decision(State.RECEIVED, ctx, by=by)


# ---------------------------------------------------------------- stage actions


def _personalize(state: State, ctx: dict, by: str) -> Decision:
    """Generate every item's model that is not ready yet."""
    out: list[Payload] = []
    oid = ctx["order"]["order_id"]
    for iid, item in ctx["items"].items():
        if item["model_status"] == "ready":
            continue
        item["model_status"], item["model_error"] = "generating", None
        out.append(GenerateNameplate(order_id=oid, item_id=iid, text=item["text"], material=item["material"],
                                     color=item["color"]))
    # Nothing to generate (every model already ready): the order waits in PERSONALIZING for its dispatch.
    return Decision(State.PERSONALIZING, ctx, out, by=by)


def _enqueue(ctx: dict, unit_key: str) -> EnqueueJob:
    iid, unit = unit_key.split(".")
    item, u, oid = ctx["items"][iid], ctx["units"][unit_key], ctx["order"]["order_id"]
    u["status"] = "queued"
    model = item["model"]
    return EnqueueJob(
        job_id=job_id(oid, iid, unit, u["attempt"]), order_id=oid, artifact_uri=model["artifact_uri"],
        material=item["material"], color=item["color"], footprint_mm=tuple(model["footprint_mm"]),
        est_grams=model["est_grams"], est_minutes=model["est_minutes"], due_by=ctx["order"].get("due_by"),
        product_type=item["product"],
    )


def _schedule(ctx: dict, units: list[str], by: str) -> Decision:
    return Decision(State.SCHEDULING, ctx, [_enqueue(ctx, k) for k in units], by=by)


def _models_ready(state: State, ctx: dict, sw: Switches, by: str) -> Decision:
    if sw.schedule:
        return _schedule(ctx, [k for k, u in ctx["units"].items() if u["status"] in ("pending", "failed")], by)
    return Decision(state, ctx, by=by)


def _attention(state: State, ctx: dict, reason: str) -> Decision:
    ctx["attention"] = {"from": state.value, "reason": reason}
    return Decision(State.NEEDS_ATTENTION, ctx,
                    [OrderNeedsAttention(order_id=ctx["order"]["order_id"], state=state, reason=reason)], reason=reason)


def _reject(state: State, ctx: dict, to: str, reason: str, by: str) -> Decision:
    return Decision(state, ctx, [OrderMoveRejected(order_id=ctx["order"]["order_id"], from_state=state, to_state=to,
                                                   reason=reason, requested_by=by)], by=by)


def _unit_of(ctx: dict, jid: str) -> str | None:
    """Map a job id back to its unit, ignoring jobs of superseded attempts."""
    oid = ctx["order"]["order_id"]
    for key, u in ctx["units"].items():
        iid, unit = key.split(".")
        if jid == job_id(oid, iid, unit, u["attempt"]):
            return key
    return None


def _on_printer(ctx: dict) -> bool:
    return any(u["status"] in ON_PRINTER for u in ctx["units"].values())


# ---------------------------------------------------------------- operator moves


def _advance(state: State, ctx: dict, msg: AdvanceOrder, sw: Switches) -> Decision:
    by, to = msg.requested_by or "unknown", msg.to_state
    oid = ctx["order"]["order_id"]
    if to == state.value:
        return _reject(state, ctx, to, "the order is already there", by)
    if to == "RECEIVED" and state == State.LEAD:
        return _received(ctx, sw, by)
    if to == "PERSONALIZING" and (state == State.RECEIVED or (state == State.NEEDS_ATTENTION
                                                               and ctx.get("attention", {}).get("from") == "PERSONALIZING")):
        for iid, text in msg.texts.items():
            item = ctx["items"].get(iid)
            if item is None:
                return _reject(state, ctx, to, f"no item {iid} in order {oid}", by)
            if not 0 < len(text.strip()) <= 40:
                return _reject(state, ctx, to, f"item {iid}: the text must be 1-40 characters", by)
            item["text"], item["model_status"], item["model"] = text.strip(), "pending", None
        ctx.pop("attention", None)
        return _personalize(state, ctx, by)
    if to == "SCHEDULING" and state == State.PERSONALIZING:
        waiting = [iid for iid, it in ctx["items"].items() if it["model_status"] != "ready"]
        if waiting:
            return _reject(state, ctx, to, f"nameplate of item {', '.join(waiting)} is not ready yet", by)
        return _models_ready(state, ctx, Switches(schedule=True), by)
    came_from = ctx.get("attention", {}).get("from")
    if to == "SCHEDULING" and state == State.NEEDS_ATTENTION and came_from in ("PRINTING", "SCHEDULING", "QC"):
        retry = [k for k, u in ctx["units"].items() if u["status"] == "failed"]
        for k in retry:
            ctx["units"][k]["attempt"] += 1
            ctx["units"][k]["tries"] = 0
        ctx.pop("attention", None)
        ctx["qc"]["rounds"] = 0
        return _schedule(ctx, retry, by)
    if to == "PRINTING":
        return _reject(state, ctx, to, "printing starts when a printer takes the plate", by)
    if to == "SHIPPED":
        return _reject(state, ctx, to, "use Ship on an order that passed QC", by)
    if to == "QC":
        return _reject(state, ctx, to, "an order reaches QC when all its units are printed", by)
    return _reject(state, ctx, to, f"cannot move from {state.value} to {to}", by)


def _qc(state: State, ctx: dict, msg: RecordQc) -> Decision:
    by = msg.inspected_by or "unknown"
    oid = ctx["order"]["order_id"]
    if state != State.QC:
        return _reject(state, ctx, "QC", "quality control applies to printed orders", by)
    if msg.passed:
        ctx["qc"]["status"] = "passed"
        return Decision(state, ctx, [OrderQcRecorded(order_id=oid, passed=True, reason=msg.reason, by=by)], by=by)
    units = msg.failed_units or list(ctx["units"])
    unknown = [k for k in units if k not in ctx["units"]]
    if unknown:
        return _reject(state, ctx, "QC", f"no unit {', '.join(unknown)} in order {oid}", by)
    ctx["qc"] = {"status": "pending", "rounds": ctx["qc"]["rounds"] + 1}
    record = OrderQcRecorded(order_id=oid, passed=False, failed_units=units, reason=msg.reason, by=by)
    for k in units:
        ctx["units"][k]["status"] = "failed"
    if ctx["qc"]["rounds"] >= MAX_QC_ROUNDS:
        d = _attention(state, ctx, f"failed QC {ctx['qc']['rounds']} times: {msg.reason or 'no reason given'}")
        d.outgoing.insert(0, record)
        return d
    for k in units:
        ctx["units"][k]["attempt"] += 1
    d = _schedule(ctx, units, by)
    d.outgoing.insert(0, record)
    d.reason = f"QC failed: {msg.reason}" if msg.reason else "QC failed"
    return d


def _ship(state: State, ctx: dict, msg: ShipOrder) -> Decision:
    by = msg.shipped_by or "unknown"
    if state != State.QC or ctx["qc"]["status"] != "passed":
        return _reject(state, ctx, "SHIPPED", "only an order that passed QC can be shipped", by)
    oid = ctx["order"]["order_id"]
    ctx["shipped"] = {"carrier": msg.carrier, "tracking": msg.tracking, "by": by}
    return Decision(State.SHIPPED, ctx, [
        OrderShipped(order_id=oid, total_agorot=ctx["order"]["total_agorot"], carrier=msg.carrier,
                     tracking=msg.tracking, by=by),
        OrderCompleted(order_id=oid),
    ], by=by)


def _cancel(state: State, ctx: dict, msg: CancelOrder) -> Decision:
    by = msg.cancelled_by or "unknown"
    if state in (State.PRINTING, State.QC) or _on_printer(ctx):
        return _reject(state, ctx, "CANCELLED", "units are on a printer: cancel the print on the printer first", by)
    oid = ctx["order"]["order_id"]
    out: list[Payload] = [OrderCancelled(order_id=oid, reason=msg.reason, by=by)]
    if any(u["status"] == "queued" for u in ctx["units"].values()):
        out.append(CancelOrderJobs(order_id=oid))
    ctx["cancelled"] = {"reason": msg.reason, "by": by, "from": state.value}
    return Decision(State.CANCELLED, ctx, out, by=by, reason=msg.reason)


# ---------------------------------------------------------------- decide


def decide(state: State, ctx: dict, msg: Payload, sw: Switches = Switches()) -> Decision:
    ctx = {**ctx, "items": {k: dict(v) for k, v in ctx.get("items", {}).items()},
           "units": {k: dict(v) for k, v in ctx.get("units", {}).items()}, "qc": dict(ctx.get("qc", {}))}
    if state in TERMINAL:
        if isinstance(msg, AdvanceOrder | RecordQc | ShipOrder | CancelOrder):
            by = getattr(msg, "requested_by", "") or getattr(msg, "inspected_by", "") or getattr(msg, "shipped_by", "") \
                or getattr(msg, "cancelled_by", "")
            return _reject(state, ctx, getattr(msg, "to_state", "?"), f"the order is {state.value.lower()}", by)
        return Decision(state, ctx, changed=False)

    match msg:
        case AdvanceOrder():
            return _advance(state, ctx, msg, sw)
        case RecordQc():
            return _qc(state, ctx, msg)
        case ShipOrder():
            return _ship(state, ctx, msg)
        case CancelOrder():
            return _cancel(state, ctx, msg)

        case ModelReady():
            item = ctx["items"].get(msg.item_id or "1")
            if item is None or item["model_status"] != "generating":
                return Decision(state, ctx, changed=False)
            item["model"] = msg.model_dump(mode="json")
            item["model_status"] = "ready"
            if state == State.PERSONALIZING and all(it["model_status"] == "ready" for it in ctx["items"].values()):
                return _models_ready(state, ctx, sw, "auto")
            return Decision(state, ctx)

        case ModelFailed():
            item = ctx["items"].get(msg.item_id or "1")
            if item is None or item["model_status"] != "generating":
                return Decision(state, ctx, changed=False)
            item["model_status"], item["model_error"] = "failed", msg.reason
            if state == State.PERSONALIZING:
                return _attention(state, ctx, f"nameplate of item {msg.item_id or '1'} failed: {msg.reason}")
            return Decision(state, ctx)

        case JobStarted():
            key = _unit_of(ctx, msg.job_id)
            if key is None:
                return Decision(state, ctx, changed=False)
            ctx["units"][key]["status"] = "printing"
            return Decision(State.PRINTING if state == State.SCHEDULING else state, ctx)

        case JobCompleted():
            key = _unit_of(ctx, msg.job_id)
            if key is None or ctx["units"][key]["status"] == "printed":
                return Decision(state, ctx, changed=False)
            ctx["units"][key]["status"] = "printed"
            if state in (State.SCHEDULING, State.PRINTING) and all(u["status"] == "printed" for u in ctx["units"].values()):
                if sw.qc_pass:
                    ctx["qc"]["status"] = "passed"
                    return Decision(State.QC, ctx, [OrderQcRecorded(order_id=ctx["order"]["order_id"], passed=True,
                                                                    reason="auto-pass switch on", by="auto")])
                return Decision(State.QC, ctx)
            return Decision(state, ctx)

        case JobFailed():
            key = _unit_of(ctx, msg.job_id)
            if key is None:
                return Decision(state, ctx, changed=False)
            u = ctx["units"][key]
            u["tries"] += 1
            if u["tries"] >= MAX_PRINT_ATTEMPTS:
                u["status"] = "failed"
                return _attention(State.PRINTING if state == State.SCHEDULING else state, ctx,
                                  f"unit {key} failed {u['tries']} times: {msg.reason}")
            u["attempt"] += 1
            return Decision(state, ctx, [_enqueue(ctx, key)])

    return Decision(state, ctx, changed=False)
