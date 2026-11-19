"""Master Coordinator: owns order sagas, the human-approval register, payments and automation settings.

It does not relay traffic between agents. It reacts to domain events,
advances the saga for the affected order, and issues the next command.
All messages of one order share ``correlationid = order_id``.
"""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.coordinator import saga
from printfarm.contracts import deterministic_id
from printfarm.contracts.catalog import UNASSIGNED
from printfarm.contracts.messages import (
    AdvanceOrder,
    ApprovalDecided,
    ApprovalRequested,
    CancelOrder,
    ConfigChanged,
    CreateOrder,
    DecideApproval,
    OrderCreated,
    OrderMoveRejected,
    OrderQcRecorded,
    OrderReceived,
    OrderStageChanged,
    OrderUpdated,
    PaymentLine,
    PaymentRecorded,
    PaymentRefunded,
    RecordPayment,
    RecordQc,
    RefundPayment,
    SetConfig,
    ShipOrder,
)
from printfarm.contracts.orders import OrderLine, line_niche, price_order


class CoordinatorAgent(Agent):
    name = "coordinator"

    # ------------------------------------------------------------ order sagas (v2: see saga.py)

    def _switches(self) -> saga.Switches:
        return saga.Switches(personalize=bool(self.setting("orders.auto_personalize")),
                             schedule=bool(self.setting("orders.auto_schedule")),
                             qc_pass=bool(self.setting("orders.auto_qc_pass")))

    async def _exists(self, conn, order_id: str) -> bool:
        row = await (await conn.execute("SELECT 1 FROM coordinator.sagas WHERE saga_id = %s", (order_id,))).fetchone()
        return row is not None

    async def _store_order(self, conn, order_id: str, *, customer_name: str, customer_ref: str, channel: str, due_by,
                           notes: str, lines: list[OrderLine], discount: int, shipping: int, created_by: str) -> int:
        pricing = price_order(lines, discount_agorot=discount, shipping_agorot=shipping)
        await conn.execute(
            "INSERT INTO coordinator.orders (order_id, customer_name, customer_ref, channel, due_by, notes, subtotal_agorot, "
            "discount_agorot, shipping_agorot, total_agorot, units, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (order_id, customer_name, customer_ref, channel, due_by, notes, pricing.subtotal, pricing.discount,
             pricing.shipping, pricing.total, pricing.units, created_by),
        )
        for i, (line, total) in enumerate(zip(lines, pricing.lines, strict=True), start=1):
            await conn.execute(
                "INSERT INTO coordinator.order_items (order_id, item_id, product, sku, text, material, color, quantity, "
                "unit_price_agorot, line_total_agorot, niche) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (order_id, str(i), line.product, line.sku, line.text, line.material, line.color, line.quantity,
                 line.unit_price, total, line_niche(line)),
            )
        return pricing.total

    @on("cmd.coordinator.create_order")
    async def create_order(self, env, cmd: CreateOrder, conn):
        if await self._exists(conn, cmd.order_id):
            self.log.info("order %s already exists", cmd.order_id)
            return
        lines = list(cmd.items)
        total = await self._store_order(conn, cmd.order_id, customer_name=cmd.customer_name, customer_ref=cmd.customer_ref,
                                        channel=cmd.channel, due_by=cmd.due_by, notes=cmd.notes, lines=lines,
                                        discount=cmd.discount_agorot, shipping=cmd.shipping_agorot,
                                        created_by=cmd.created_by or "unknown")
        d = saga.start_created(cmd, self._switches())
        await self.emit(conn, OrderCreated(order_id=cmd.order_id, customer_name=cmd.customer_name, channel=cmd.channel,
                                           items=lines, total_agorot=total, units=sum(x.quantity for x in lines),
                                           state=d.state), cause=env, correlationid=cmd.order_id)
        await self._start(conn, env, cmd.order_id, d, cmd.due_by)

    @on("evt.order.order.received")
    async def order_received(self, env, msg: OrderReceived, conn):
        """A v1 order (CLI, automated channels): a one-line order with an unknown price."""
        if await self._exists(conn, msg.order_id):
            self.log.info("order %s already has a saga", msg.order_id)
            return
        d = saga.start_legacy(msg, self._switches())
        item = d.context["items"]["1"]
        line = OrderLine(product=item["product"], text=item["text"], material=item["material"], color=item["color"],
                         quantity=item["quantity"], unit_price_agorot=0)
        await self._store_order(conn, msg.order_id, customer_name=msg.customer_ref, customer_ref=msg.customer_ref,
                                channel="api", due_by=msg.due_by, notes="", lines=[line], discount=0, shipping=0,
                                created_by="api")
        await self._start(conn, env, msg.order_id, d, msg.due_by)

    async def _start(self, conn, env, order_id: str, d: saga.Decision, due_by) -> None:
        await conn.execute(
            "INSERT INTO coordinator.sagas (saga_id, saga_type, state, context, deadline_at) VALUES (%s, 'order', %s, %s, %s)",
            (order_id, d.state, Jsonb(d.context), due_by),
        )
        await self._history(conn, order_id, None, d.state, env)
        await self.emit(conn, OrderStageChanged(order_id=order_id, from_state=None, to_state=d.state, by=d.by),
                        cause=env, correlationid=order_id)
        for out in d.outgoing:
            await self.emit(conn, out, cause=env, correlationid=order_id)

    # Operator moves on the board: each one ends in a stage change or evt.order.move.rejected.
    @on("cmd.coordinator.advance_order")
    async def advance_order(self, env, cmd: AdvanceOrder, conn):
        await self._advance(env, cmd, conn)

    @on("cmd.coordinator.record_qc")
    async def record_qc(self, env, cmd: RecordQc, conn):
        await self._advance(env, cmd, conn)

    @on("cmd.coordinator.ship_order")
    async def ship_order(self, env, cmd: ShipOrder, conn):
        await self._advance(env, cmd, conn)

    @on("cmd.coordinator.cancel_order")
    async def cancel_order(self, env, cmd: CancelOrder, conn):
        await self._advance(env, cmd, conn)

    @on("evt.personalization.model.>")
    async def model_event(self, env, msg, conn):
        await self._advance(env, msg, conn)

    @on("evt.fleet.job.>")
    async def job_event(self, env, msg, conn):
        await self._advance(env, msg, conn)

    async def _advance(self, env, msg, conn) -> None:
        order_id = msg.order_id
        row = await (
            await conn.execute(
                "SELECT state, context, version FROM coordinator.sagas WHERE saga_id = %s FOR UPDATE", (order_id,)
            )
        ).fetchone()
        if row is None:
            self.log.warning("no saga for order %s (%s)", order_id, env.type)
            if env.kind == "command":  # tell the operator instead of failing silently
                await self.emit(conn, OrderMoveRejected(order_id=order_id, from_state="", to_state=getattr(msg, "to_state", ""),
                                                        reason=f"no order {order_id}"), cause=env, correlationid=order_id)
            return
        d = saga.decide(saga.State(row["state"]), row["context"], msg, self._switches())
        if not d.changed:
            return
        await conn.execute(
            "UPDATE coordinator.sagas SET state = %s, context = %s, version = version + 1, updated_at = now() "
            "WHERE saga_id = %s AND version = %s",
            (d.state, Jsonb(d.context), order_id, row["version"]),
        )
        if d.state != row["state"]:
            await self._history(conn, order_id, row["state"], d.state, env)
            await self.emit(conn, OrderStageChanged(order_id=order_id, from_state=row["state"], to_state=d.state, by=d.by,
                                                    reason=d.reason), cause=env, correlationid=order_id)
            self.log.info("order %s: %s -> %s (%s)", order_id, row["state"], d.state, d.by)
        elif d.context != row["context"]:
            # Progress without a stage change (e.g. one nameplate ready): tell screens the stored order changed, so
            # they never keep showing a state older than the database.
            cause = env.type.removeprefix("io.printfarm.").rsplit(".v", 1)[0]
            await self.emit(conn, OrderUpdated(order_id=order_id, state=d.state, cause=cause), cause=env,
                            correlationid=order_id)
        for out in d.outgoing:
            if isinstance(out, OrderQcRecorded):
                await conn.execute(
                    "INSERT INTO coordinator.qc_checks (order_id, passed, units_failed, units_checked, reason, checked_by, "
                    "failed_keys, checklist) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (order_id, out.passed, len(out.failed_units), len(d.context["units"]), out.reason, out.by,
                     Jsonb(sorted(out.failed_units)), Jsonb([c.model_dump() for c in out.checklist])))
            await self.emit(conn, out, cause=env, correlationid=order_id)

    async def _history(self, conn, saga_id, from_state, to_state, env) -> None:
        await conn.execute(
            "INSERT INTO coordinator.saga_history (saga_id, from_state, to_state, message_id, message_type) "
            "VALUES (%s, %s, %s, %s, %s)",
            (saga_id, from_state, to_state, env.id, env.type),
        )

    # ------------------------------------------------------------ approvals (human in the loop)

    @on("evt.approval.request.created")
    async def approval_requested(self, env, msg: ApprovalRequested, conn):
        await conn.execute(
            "INSERT INTO coordinator.approvals (approval_id, action, requested_by, summary, details) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (msg.approval_id, msg.action, msg.requested_by, msg.summary, Jsonb(msg.details)),
        )
        self.log.warning("APPROVAL NEEDED [%s] %s: %s", msg.approval_id, msg.action, msg.summary)

    @on("cmd.coordinator.decide_approval")
    async def decide_approval(self, env, msg: DecideApproval, conn):
        row = await (
            await conn.execute(
                "UPDATE coordinator.approvals SET status = %s, decided_by = %s, note = %s, decided_at = now() "
                "WHERE approval_id = %s AND status = 'pending' RETURNING action",
                ("approved" if msg.approved else "rejected", msg.decided_by, msg.note, msg.approval_id),
            )
        ).fetchone()
        if row is None:
            self.log.warning("approval %s not pending; decision ignored", msg.approval_id)
            return
        await self.emit(
            conn,
            ApprovalDecided(
                approval_id=msg.approval_id, action=row["action"], approved=msg.approved,
                decided_by=msg.decided_by, note=msg.note,
            ),
            cause=env,
        )

    # ------------------------------------------------------------ payments (income is booked by the Finance agent)

    @on("cmd.coordinator.record_payment")
    async def record_payment(self, env, cmd: RecordPayment, conn):
        await self._payment(env, cmd, conn, refund=False)

    @on("cmd.coordinator.refund_payment")
    async def refund_payment(self, env, cmd: RefundPayment, conn):
        await self._payment(env, cmd, conn, refund=True)

    async def _payment(self, env, cmd: RecordPayment | RefundPayment, conn, *, refund: bool) -> None:
        at = (cmd.refunded_at if refund else cmd.paid_at) or env.time
        cur = await conn.execute(
            "INSERT INTO coordinator.payments (payment_id, order_id, kind, amount_agorot, method, at, receipt_no, recorded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (env.id, cmd.order_id, "refund" if refund else "payment", cmd.amount_agorot, cmd.method.value, at,
             cmd.receipt_no, cmd.recorded_by or "unknown"),
        )
        if not cur.rowcount:
            return
        product_type = cmd.product_type
        lines: list[PaymentLine] = []
        if cmd.order_id:
            # The order's lines (a catalog line by its SKU), so Finance splits the money per SKU and niche. The
            # entry's own product_type stays a summary: the one product, or "mixed".
            rows = await (await conn.execute(
                "SELECT CASE WHEN product = 'catalog' AND sku <> '' THEN sku ELSE product END AS product, niche, "
                "line_total_agorot, quantity FROM coordinator.order_items WHERE order_id = %s ORDER BY item_id::int",
                (cmd.order_id,))).fetchall()
            priced = any(r["line_total_agorot"] > 0 for r in rows)
            lines = [PaymentLine(product_type=r["product"], niche=r["niche"] or UNASSIGNED,
                                 weight_agorot=r["line_total_agorot"] if priced else r["quantity"]) for r in rows]
            products = sorted({r["product"] for r in rows})
            if not products:
                self.log.warning("payment for unknown order %s recorded anyway", cmd.order_id)
            elif product_type is None:
                product_type = products[0] if len(products) == 1 else "mixed"
        common = {"payment_id": env.id, "order_id": cmd.order_id, "amount_agorot": cmd.amount_agorot,
                  "method": cmd.method, "receipt_no": cmd.receipt_no, "product_type": product_type, "lines": lines}
        event = PaymentRefunded(**common, refunded_at=at) if refund else PaymentRecorded(**common, paid_at=at)
        await self.emit(conn, event, cause=env, correlationid=cmd.order_id or env.correlationid,
                        id=deterministic_id("refund" if refund else "payment", env.id))

    # ------------------------------------------------------------ automation settings (NATS KV "config", audited)

    @on("cmd.coordinator.set_config")
    async def set_config(self, env, cmd: SetConfig, conn):
        raw = await self.bus.kv_get("config", cmd.key)
        previous = json.loads(raw) if raw else None
        cur = await conn.execute(
            "INSERT INTO coordinator.config_changes (message_id, key, value, previous, changed_by) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (message_id) DO NOTHING",
            (env.id, cmd.key, Jsonb(cmd.value), Jsonb(previous), cmd.changed_by or "unknown"),
        )
        if not cur.rowcount:
            return
        await self.bus.kv_put("config", cmd.key, json.dumps(cmd.value).encode())
        self.log.info("config %s = %r (was %r) by %s", cmd.key, cmd.value, previous, cmd.changed_by)
        await self.emit(conn, ConfigChanged(key=cmd.key, value=cmd.value, previous=previous, changed_by=cmd.changed_by),
                        cause=env)
