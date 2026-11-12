"""Inventory Agent: filament ledger, reorder policy, purchase orders (with approval gate)."""

from __future__ import annotations

import uuid

from printfarm.agents.base import Agent, on
from printfarm.agents.inventory.model import Policy, decide
from printfarm.contracts import new_message
from printfarm.contracts.messages import (
    ApprovalDecided,
    ApprovalRequested,
    AvailabilityReply,
    FilamentConsumed,
    PlateFinished,
    ReorderPlaced,
    StockLow,
)


class InventoryAgent(Agent):
    name = "inventory"

    async def on_start(self) -> None:
        async with self.db.tx() as conn:
            for m in self.fleet.materials:
                await conn.execute(
                    "INSERT INTO inventory.materials (material, color, grams_per_spool, cost_per_spool, lead_time_days, "
                    "safety_stock_days, review_period_days, baseline_daily_grams, auto_approve_limit) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (material, color) DO UPDATE SET "
                    "grams_per_spool = EXCLUDED.grams_per_spool, cost_per_spool = EXCLUDED.cost_per_spool, "
                    "lead_time_days = EXCLUDED.lead_time_days, safety_stock_days = EXCLUDED.safety_stock_days, "
                    "review_period_days = EXCLUDED.review_period_days, "
                    "baseline_daily_grams = EXCLUDED.baseline_daily_grams, auto_approve_limit = EXCLUDED.auto_approve_limit",
                    (m.material, m.color, m.grams_per_spool, m.cost_per_spool, m.lead_time_days,
                     m.safety_stock_days, m.review_period_days, m.baseline_daily_grams, m.auto_approve_limit),
                )
            for s in self.fleet.spools:  # seed only; afterwards the ledger is the truth
                await conn.execute(
                    "INSERT INTO inventory.spools (spool_id, material, color, initial_grams, remaining_grams, printer_id, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (s.id, s.material, s.color, s.grams, s.remaining_grams if s.remaining_grams is not None else s.grams,
                     s.loaded_on, "loaded" if s.loaded_on else "stock"),
                )
        await self.bus.serve("qry.inventory.availability", self._availability)

    # ------------------------------------------------------------ consumption

    @on("evt.fleet.plate.finished")
    async def plate_finished(self, env, ev: PlateFinished, conn):
        if ev.grams_used <= 0:
            return
        spool = await (
            await conn.execute(
                "SELECT * FROM inventory.spools WHERE printer_id = %s AND material = %s AND color = %s AND status = 'loaded' "
                "ORDER BY remaining_grams LIMIT 1 FOR UPDATE",
                (ev.printer_id, ev.material, ev.color),
            )
        ).fetchone()
        spool_id = spool["spool_id"] if spool else None
        remaining = max((spool["remaining_grams"] if spool else 0) - ev.grams_used, 0)
        if spool:
            await conn.execute(
                "UPDATE inventory.spools SET remaining_grams = %s, status = CASE WHEN %s <= 0 THEN 'empty' ELSE status END "
                "WHERE spool_id = %s",
                (remaining, remaining, spool_id),
            )
        else:
            self.log.warning("no loaded %s/%s spool registered on %s", ev.material, ev.color, ev.printer_id)
        await conn.execute(
            "INSERT INTO inventory.ledger (spool_id, material, color, delta_grams, reason, ref) VALUES (%s, %s, %s, %s, %s, %s)",
            (spool_id, ev.material, ev.color, -ev.grams_used, "print" if ev.succeeded else "failed_print", ev.plate_id),
        )
        await self.emit(
            conn,
            FilamentConsumed(spool_id=spool_id or "unknown", material=ev.material, color=ev.color,
                             grams=ev.grams_used, remaining_grams=remaining),
            cause=env,
        )
        await self._check_reorder(conn, ev.material, ev.color, env)

    async def _check_reorder(self, conn, material: str, color: str, cause) -> None:
        m = await (
            await conn.execute("SELECT * FROM inventory.materials WHERE material = %s AND color = %s FOR UPDATE",
                               (material, color))
        ).fetchone()
        if m is None:
            return
        stats = await (
            await conn.execute(
                """SELECT
                     (SELECT coalesce(sum(remaining_grams), 0) FROM inventory.spools
                       WHERE material = %(m)s AND color = %(c)s AND status <> 'empty') AS on_hand,
                     (SELECT coalesce(sum(spools), 0) FROM inventory.purchase_orders
                       WHERE material = %(m)s AND color = %(c)s AND status IN ('pending_approval', 'placed')) AS on_order_spools,
                     (SELECT coalesce(-sum(delta_grams), 0) FROM inventory.ledger
                       WHERE material = %(m)s AND color = %(c)s AND delta_grams < 0 AND at > now() - interval '14 days') AS used_14d
                """,
                {"m": material, "c": color},
            )
        ).fetchone()
        policy = Policy(m["grams_per_spool"], m["cost_per_spool"], m["lead_time_days"], m["safety_stock_days"],
                        m["review_period_days"], m["baseline_daily_grams"], m["auto_approve_limit"])
        d = decide(policy, stats["on_hand"], stats["on_order_spools"] * m["grams_per_spool"], stats["used_14d"])
        if d is None:
            return
        await self.emit(conn, StockLow(material=material, color=color, on_hand_grams=stats["on_hand"],
                                       reorder_point_grams=d.reorder_point), cause=cause)
        po_id = f"po-{uuid.uuid4().hex[:10]}"
        if d.needs_approval:
            approval_id = f"apr-{uuid.uuid4().hex[:10]}"
            await conn.execute(
                "INSERT INTO inventory.purchase_orders (po_id, material, color, spools, est_cost, status, approval_id) "
                "VALUES (%s, %s, %s, %s, %s, 'pending_approval', %s)",
                (po_id, material, color, d.spools, d.est_cost, approval_id),
            )
            await self.emit(
                conn,
                ApprovalRequested(
                    approval_id=approval_id, action="inventory.reorder", requested_by=self.name,
                    summary=f"Order {d.spools} x {material} {color} spools (~{d.est_cost:.2f})",
                    details={"po_id": po_id, "on_hand_grams": stats["on_hand"], "reorder_point": d.reorder_point},
                ),
                cause=cause, correlationid=po_id,
            )
        else:
            await conn.execute(
                "INSERT INTO inventory.purchase_orders (po_id, material, color, spools, est_cost, status) "
                "VALUES (%s, %s, %s, %s, %s, 'placed')",
                (po_id, material, color, d.spools, d.est_cost),
            )
            await self._placed(conn, po_id, material, color, d.spools, d.est_cost, cause)

    async def _placed(self, conn, po_id, material, color, spools, cost, cause) -> None:
        # Supplier integration (email / supplier API via an MCP tool server) hooks in here.
        await self.emit(conn, ReorderPlaced(po_id=po_id, material=material, color=color, spools=spools, est_cost=cost),
                        cause=cause, correlationid=po_id)

    @on("evt.approval.request.decided")
    async def approval_decided(self, env, ev: ApprovalDecided, conn):
        if ev.action != "inventory.reorder":
            return
        po = await (
            await conn.execute(
                "UPDATE inventory.purchase_orders SET status = %s WHERE approval_id = %s AND status = 'pending_approval' "
                "RETURNING *",
                ("placed" if ev.approved else "rejected", ev.approval_id),
            )
        ).fetchone()
        if po and ev.approved:
            await self._placed(conn, po["po_id"], po["material"], po["color"], po["spools"], po["est_cost"], env)

    # ------------------------------------------------------------ queries

    async def _availability(self, env):
        q = env.payload()
        async with self.db.tx() as conn:
            row = await (
                await conn.execute(
                    "SELECT coalesce(sum(remaining_grams), 0) AS on_hand FROM inventory.spools "
                    "WHERE material = %s AND color = %s AND status <> 'empty'",
                    (q.material, q.color),
                )
            ).fetchone()
        # reserved_grams: fleet-side reservations are a phase-2 feature (see 04-state-management.md)
        reply = AvailabilityReply(material=q.material, color=q.color, on_hand_grams=row["on_hand"], reserved_grams=0.0)
        return new_message(reply, source=self.source, cause=env)
