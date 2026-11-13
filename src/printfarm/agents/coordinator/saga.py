"""Order saga as a pure state machine: (state, message) -> (new state, messages to send).

No I/O here, so every transition is unit-testable. The coordinator agent
loads the saga row, calls ``decide`` and persists the result together with
the outgoing commands in one transaction.

    RECEIVED --GenerateNameplate--> MODELING
    MODELING --ModelReady/EnqueueJob xN--> QUEUED
    MODELING --ModelFailed--> NEEDS_ATTENTION
    QUEUED   --JobStarted--> PRINTING
    QUEUED|PRINTING --JobCompleted (all units)--> COMPLETED
    QUEUED|PRINTING --JobFailed--> (re-enqueue, attempts < MAX) | NEEDS_ATTENTION
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from printfarm.contracts import Payload
from printfarm.contracts.messages import (
    EnqueueJob,
    GenerateNameplate,
    JobCompleted,
    JobFailed,
    JobStarted,
    ModelFailed,
    ModelReady,
    OrderCompleted,
    OrderNeedsAttention,
    OrderReceived,
)

MAX_PRINT_ATTEMPTS = 2


class State(StrEnum):
    RECEIVED = "RECEIVED"
    MODELING = "MODELING"
    QUEUED = "QUEUED"
    PRINTING = "PRINTING"
    COMPLETED = "COMPLETED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    CANCELLED = "CANCELLED"


TERMINAL = {State.COMPLETED, State.CANCELLED}


@dataclass
class Decision:
    state: State
    context: dict[str, Any]
    outgoing: list[Payload] = field(default_factory=list)
    changed: bool = True


def unchanged(state: State, ctx: dict) -> Decision:
    return Decision(state, ctx, [], changed=False)


def job_id(order_id: str, unit: int, attempt: int) -> str:
    return f"{order_id}-u{unit}-a{attempt}"


def start(order: OrderReceived) -> Decision:
    ctx = {
        "order": order.model_dump(mode="json"),
        "units": {str(u): {"attempt": 1, "status": "pending"} for u in range(1, order.quantity + 1)},
    }
    cmd = GenerateNameplate(order_id=order.order_id, text=order.text, material=order.material, color=order.color)
    return Decision(State.MODELING, ctx, [cmd])


def _enqueue(ctx: dict, unit: str) -> EnqueueJob:
    order, model, u = ctx["order"], ctx["model"], ctx["units"][unit]
    return EnqueueJob(
        job_id=job_id(order["order_id"], int(unit), u["attempt"]),
        order_id=order["order_id"],
        artifact_uri=model["artifact_uri"],
        material=order["material"],
        color=order["color"],
        footprint_mm=tuple(model["footprint_mm"]),
        est_grams=model["est_grams"],
        est_minutes=model["est_minutes"],
        due_by=order.get("due_by"),
        product_type=order.get("product", "nameplate"),
    )


def _unit_of(ctx: dict, jid: str) -> str | None:
    """Map a job id back to its unit, ignoring jobs from superseded attempts."""
    order_id = ctx["order"]["order_id"]
    for unit, u in ctx["units"].items():
        if jid == job_id(order_id, int(unit), u["attempt"]):
            return unit
    return None


def decide(state: State, ctx: dict, msg: Payload) -> Decision:
    ctx = {**ctx, "units": {k: dict(v) for k, v in ctx.get("units", {}).items()}}
    if state in TERMINAL:
        return unchanged(state, ctx)

    match msg:
        case ModelReady() if state == State.MODELING:
            ctx["model"] = msg.model_dump(mode="json")
            out = []
            for unit in ctx["units"]:
                ctx["units"][unit]["status"] = "queued"
                out.append(_enqueue(ctx, unit))
            return Decision(State.QUEUED, ctx, out)

        case ModelFailed() if state == State.MODELING:
            return Decision(
                State.NEEDS_ATTENTION, ctx,
                [OrderNeedsAttention(order_id=ctx["order"]["order_id"], state=state, reason=msg.reason)],
            )

        case JobStarted():
            unit = _unit_of(ctx, msg.job_id)
            if unit is None:
                return unchanged(state, ctx)
            ctx["units"][unit]["status"] = "printing"
            return Decision(State.PRINTING if state == State.QUEUED else state, ctx, [])

        case JobCompleted():
            unit = _unit_of(ctx, msg.job_id)
            if unit is None or ctx["units"][unit]["status"] == "done":
                return unchanged(state, ctx)
            ctx["units"][unit]["status"] = "done"
            if all(u["status"] == "done" for u in ctx["units"].values()):
                return Decision(State.COMPLETED, ctx, [OrderCompleted(order_id=ctx["order"]["order_id"])])
            return Decision(state, ctx, [])

        case JobFailed():
            unit = _unit_of(ctx, msg.job_id)
            if unit is None:
                return unchanged(state, ctx)
            u = ctx["units"][unit]
            if u["attempt"] >= MAX_PRINT_ATTEMPTS:
                u["status"] = "failed"
                return Decision(
                    State.NEEDS_ATTENTION, ctx,
                    [OrderNeedsAttention(
                        order_id=ctx["order"]["order_id"], state=state,
                        reason=f"unit {unit} failed {u['attempt']} times: {msg.reason}",
                    )],
                )
            u["attempt"] += 1
            u["status"] = "queued"
            return Decision(state, ctx, [_enqueue(ctx, unit)])

    return unchanged(state, ctx)
