"""Master Coordinator: owns order sagas and the human-approval register.

It does not relay traffic between agents. It reacts to domain events,
advances the saga for the affected order, and issues the next command.
All messages of one order share ``correlationid = order_id``.
"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.coordinator import saga
from printfarm.contracts.messages import ApprovalDecided, ApprovalRequested, DecideApproval, OrderReceived


class CoordinatorAgent(Agent):
    name = "coordinator"

    # ------------------------------------------------------------ sagas

    @on("evt.order.order.received")
    async def order_received(self, env, msg: OrderReceived, conn):
        exists = await (await conn.execute("SELECT 1 FROM coordinator.sagas WHERE saga_id = %s", (msg.order_id,))).fetchone()
        if exists:
            self.log.info("order %s already has a saga", msg.order_id)
            return
        d = saga.start(msg)
        await conn.execute(
            "INSERT INTO coordinator.sagas (saga_id, saga_type, state, context, deadline_at) VALUES (%s, 'order', %s, %s, %s)",
            (msg.order_id, d.state, Jsonb(d.context), msg.due_by),
        )
        await self._history(conn, msg.order_id, None, d.state, env)
        for out in d.outgoing:
            await self.emit(conn, out, cause=env, correlationid=msg.order_id)

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
            return
        d = saga.decide(saga.State(row["state"]), row["context"], msg)
        if not d.changed:
            return
        await conn.execute(
            "UPDATE coordinator.sagas SET state = %s, context = %s, version = version + 1, updated_at = now() "
            "WHERE saga_id = %s AND version = %s",
            (d.state, Jsonb(d.context), order_id, row["version"]),
        )
        if d.state != row["state"]:
            await self._history(conn, order_id, row["state"], d.state, env)
            self.log.info("order %s: %s -> %s", order_id, row["state"], d.state)
        for out in d.outgoing:
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
