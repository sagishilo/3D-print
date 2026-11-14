"""Inventory Agent: spools, the filament ledger, reorder rules, purchase orders (with approval gate) and alerts.

Filament flow:
    evt.fleet.plate.scheduled  -> reservation of the plate's grams on its printer; the loaded spools are checked
                                  (spool_short alert when they cannot finish it)
    evt.fleet.plate.finished   -> grams booked on the printer's loaded spools of that material and color, lowest first
                                  (an AMS moves to the next spool); reservation released; spool_empty / low_stock
                                  alerts and the reorder rule re-evaluated
    register / adjust (weigh) / load spools, receive POs      -> same re-evaluation
While a plate prints, the dashboard shows each active spool going down from the printer's live progress; the ledger
is written once, when the plate finishes (exact grams for a finished plate, the printed share for a failed one).

Purchase-order lifecycle:
    pending_approval --approved--> placed --confirm_po_ordered--> ordered --receive_po--> received
    pending_approval --rejected--> rejected
"placed" means the rule (or a human) decided to buy; "ordered" means a human actually ordered it from the supplier
and recorded the real total, which is what the Finance agent books as an expense.

Alerts (inventory.alerts, at most one open per subject) close on their own when the condition clears, or when a
person acknowledges them.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from printfarm.agents.base import Agent, on
from printfarm.agents.inventory.model import Policy, assess, check_spools, decide, spools_to_order
from printfarm.contracts import new_message
from printfarm.contracts.messages import (
    AcknowledgeInventoryAlert,
    AdjustSpool,
    ApprovalDecided,
    ApprovalRequested,
    AvailabilityReply,
    ConfirmPoOrdered,
    FilamentConsumed,
    InventoryAlertRaised,
    InventoryAlertResolved,
    LoadSpool,
    PlateFinished,
    PlateScheduled,
    PoReceived,
    PurchaseOrdered,
    ReceivePo,
    RegisterSpool,
    ReorderPlaced,
    ReorderRuleChanged,
    RequestReorder,
    SetReorderRule,
    SpoolAdjusted,
    SpoolLoaded,
    SpoolRegistered,
    StockLow,
    UpdatePo,
)

OPEN_PO_STATUSES = ("pending_approval", "placed", "ordered")  # count towards the stock position


def ils(agorot: int) -> str:
    return f"{agorot / 100:,.2f} ILS"


def policy_of(m) -> Policy:
    return Policy(m["grams_per_spool"], m["cost_per_spool_agorot"], m["lead_time_days"], m["safety_stock_days"],
                  m["review_period_days"], m["baseline_daily_grams"], m["auto_approve_limit_agorot"],
                  m["min_stock_grams"], m["reorder_mode"])


class InventoryAgent(Agent):
    name = "inventory"

    async def on_start(self) -> None:
        async with self.db.tx() as conn:
            # Config seeds new materials only: once a material exists, its rule is edited on the dashboard.
            for m in self.fleet.materials:
                await conn.execute(
                    "INSERT INTO inventory.materials (material, color, grams_per_spool, cost_per_spool_agorot, lead_time_days, "
                    "safety_stock_days, review_period_days, baseline_daily_grams, auto_approve_limit_agorot, min_stock_grams, "
                    "reorder_mode, supplier) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (m.material, m.color, m.grams_per_spool, m.cost_per_spool_agorot, m.lead_time_days,
                     m.safety_stock_days, m.review_period_days, m.baseline_daily_grams, m.auto_approve_limit_agorot,
                     m.min_stock_grams, m.reorder_mode, m.supplier),
                )
            for s in self.fleet.spools:  # seed only; afterwards the ledger is the truth
                await conn.execute(
                    "INSERT INTO inventory.spools (spool_id, material, color, initial_grams, remaining_grams, printer_id, "
                    "status, brand, slot, location) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (s.id, s.material, s.color, s.grams, s.remaining_grams if s.remaining_grams is not None else s.grams,
                     s.loaded_on, "loaded" if s.loaded_on else "stock", s.brand, s.slot if s.loaded_on else None,
                     None if s.loaded_on else s.location),
                )
        await self.bus.serve("qry.inventory.availability", self._availability)

    # ------------------------------------------------------------ alerts

    async def _raise(self, conn, cause, *, kind: str, severity: str, subject: str, material: str, color: str,
                     message: str, **ids) -> None:
        alert_id = f"inv-{uuid.uuid4().hex[:10]}"
        row = await (await conn.execute(
            "INSERT INTO inventory.alerts (alert_id, kind, severity, subject, material, color, spool_id, printer_id, "
            "plate_id, needed_grams, available_grams, message) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (kind, subject) WHERE resolved_at IS NULL DO NOTHING RETURNING alert_id",
            (alert_id, kind, severity, subject, material, color, ids.get("spool_id"), ids.get("printer_id"),
             ids.get("plate_id"), ids.get("needed_grams"), ids.get("available_grams"), message),
        )).fetchone()
        if row:  # a new alert (an open one for the same subject stays as it is)
            self.log.warning("ALERT %s: %s", kind, message)
            await self.emit(conn, InventoryAlertRaised(alert_id=alert_id, alert_kind=kind, severity=severity, material=material,
                                                       color=color, message=message, **ids), cause=cause)

    async def _resolve(self, conn, cause, *, kind: str, subject: str, by: str = "auto") -> None:
        rows = await (await conn.execute(
            "UPDATE inventory.alerts SET resolved_at = now(), resolved_by = %s WHERE kind = %s AND subject = %s "
            "AND resolved_at IS NULL RETURNING alert_id", (by, kind, subject))).fetchall()
        for r in rows:
            await self.emit(conn, InventoryAlertResolved(alert_id=r["alert_id"], alert_kind=kind, by=by), cause=cause)

    # ------------------------------------------------------------ evaluation

    async def _material(self, conn, material: str, color: str, *, lock: bool = False):
        return await (await conn.execute(
            "SELECT * FROM inventory.materials WHERE material = %s AND color = %s" + (" FOR UPDATE" if lock else ""),
            (material, color))).fetchone()

    async def _position(self, conn, material: str, color: str) -> dict:
        return await (await conn.execute(
            """SELECT
                 (SELECT coalesce(sum(remaining_grams), 0) FROM inventory.spools
                   WHERE material = %(m)s AND color = %(c)s AND status <> 'empty') AS on_hand,
                 (SELECT coalesce(sum(spools), 0) FROM inventory.purchase_orders
                   WHERE material = %(m)s AND color = %(c)s AND status = ANY(%(open)s)) AS on_order_spools,
                 (SELECT coalesce(-sum(delta_grams), 0) FROM inventory.ledger
                   WHERE material = %(m)s AND color = %(c)s AND delta_grams < 0 AND reason IN ('print', 'failed_print')
                     AND at > now() - interval '14 days') AS used_14d""",
            {"m": material, "c": color, "open": list(OPEN_PO_STATUSES)})).fetchone()

    async def _evaluate(self, conn, material: str, color: str, cause, *, reorder: bool = True) -> None:
        """Low-stock alert and the reorder rule for one material and color."""
        m = await self._material(conn, material, color, lock=True)
        if m is None:
            return
        pos = await self._position(conn, material, color)
        policy = policy_of(m)
        subject = f"{material}/{color}"
        if pos["on_hand"] < m["min_stock_grams"]:
            await self._raise(conn, cause, kind="low_stock", severity="warning", subject=subject, material=material,
                              color=color, needed_grams=m["min_stock_grams"], available_grams=pos["on_hand"],
                              message=f"{material} {color}: {pos['on_hand']:.0f} g left, below the minimum of "
                                      f"{m['min_stock_grams']:.0f} g")
        else:
            await self._resolve(conn, cause, kind="low_stock", subject=subject)
        if not reorder:
            return
        d = decide(policy, pos["on_hand"], pos["on_order_spools"] * m["grams_per_spool"], pos["used_14d"])
        if d is None:
            return
        if not self.setting("inventory.auto_reorder") and not d.needs_approval:
            d = replace(d, needs_approval=True)  # automation switched off on the dashboard: a human decides
        await self.emit(conn, StockLow(material=material, color=color, on_hand_grams=pos["on_hand"],
                                       reorder_point_grams=d.reorder_point), cause=cause)
        await self._new_po(conn, cause, material, color, d.spools, d.est_cost_agorot, needs_approval=d.needs_approval,
                           why=f"{pos['on_hand']:.0f} g on hand, rule triggers at {d.reorder_point:.0f} g")

    async def _new_po(self, conn, cause, material: str, color: str, spools: int, cost: int, *, needs_approval: bool,
                      why: str) -> str:
        po_id = f"po-{uuid.uuid4().hex[:10]}"
        if needs_approval:
            approval_id = f"apr-{uuid.uuid4().hex[:10]}"
            await conn.execute(
                "INSERT INTO inventory.purchase_orders (po_id, material, color, spools, est_cost_agorot, status, approval_id) "
                "VALUES (%s, %s, %s, %s, %s, 'pending_approval', %s)",
                (po_id, material, color, spools, cost, approval_id),
            )
            await self.emit(conn, ApprovalRequested(
                approval_id=approval_id, action="inventory.reorder", requested_by=self.name,
                summary=f"Order {spools} x {material} {color} spools (~{ils(cost)})",
                details={"po_id": po_id, "why": why, "est_cost_agorot": cost},
            ), cause=cause, correlationid=po_id)
        else:
            await conn.execute(
                "INSERT INTO inventory.purchase_orders (po_id, material, color, spools, est_cost_agorot, status) "
                "VALUES (%s, %s, %s, %s, %s, 'placed')",
                (po_id, material, color, spools, cost),
            )
            await self._placed(conn, po_id, material, color, spools, cost, cause)
        return po_id

    async def _check_printer(self, conn, printer_id: str, cause) -> None:
        """Can the printer's loaded spools finish its scheduled plates, in order? (spool_short alerts)"""
        plates = await (await conn.execute(
            "SELECT * FROM inventory.reservations WHERE printer_id = %s ORDER BY created_at, plate_id",
            (printer_id,))).fetchall()
        before: dict[tuple[str, str], float] = {}
        for r in plates:
            key = (r["material"], r["color"])
            loaded = await (await conn.execute(
                "SELECT coalesce(sum(remaining_grams), 0) AS g FROM inventory.spools WHERE printer_id = %s "
                "AND material = %s AND color = %s AND status = 'loaded'", (printer_id, *key))).fetchone()
            chk = check_spools(r["grams"], loaded["g"], before.get(key, 0.0))
            before[key] = before.get(key, 0.0) + r["grams"]
            if chk.short:
                await self._raise(
                    conn, cause, kind="spool_short", severity="warning", subject=r["plate_id"], material=key[0],
                    color=key[1], printer_id=printer_id, plate_id=r["plate_id"], needed_grams=chk.needed_grams,
                    available_grams=chk.available_grams,
                    message=f"{printer_id}: plate {r['plate_id']} needs ~{chk.needed_grams:.0f} g {key[0]} {key[1]}, "
                            f"the loaded spools have {chk.available_grams:.0f} g left for it: load another spool")
            else:
                await self._resolve(conn, cause, kind="spool_short", subject=r["plate_id"])

    # ------------------------------------------------------------ consumption

    @on("evt.fleet.plate.scheduled")
    async def plate_scheduled(self, env, ev: PlateScheduled, conn):
        await conn.execute(
            "INSERT INTO inventory.reservations (plate_id, printer_id, material, color, grams) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING", (ev.plate_id, ev.printer_id, ev.material, ev.color, ev.est_grams))
        await self._check_printer(conn, ev.printer_id, env)

    @on("evt.fleet.plate.finished")
    async def plate_finished(self, env, ev: PlateFinished, conn):
        await conn.execute("DELETE FROM inventory.reservations WHERE plate_id = %s", (ev.plate_id,))
        await self._resolve(conn, env, kind="spool_short", subject=ev.plate_id)
        if ev.grams_used > 0:
            await self._consume(conn, env, ev)
            await self._evaluate(conn, ev.material, ev.color, env)
        await self._check_printer(conn, ev.printer_id, env)

    async def _consume(self, conn, env, ev: PlateFinished) -> None:
        spools = await (await conn.execute(
            "SELECT * FROM inventory.spools WHERE printer_id = %s AND material = %s AND color = %s AND status = 'loaded' "
            "ORDER BY remaining_grams, spool_id FOR UPDATE", (ev.printer_id, ev.material, ev.color))).fetchall()
        reason = "print" if ev.succeeded else "failed_print"
        left = ev.grams_used
        if not spools:
            self.log.warning("no loaded %s/%s spool registered on %s", ev.material, ev.color, ev.printer_id)
        for i, s in enumerate(spools):  # lowest first; an AMS continues on the next spool of the same filament
            take = left if i == len(spools) - 1 else min(left, s["remaining_grams"])
            if take <= 0:
                break
            remaining = max(s["remaining_grams"] - take, 0.0)
            await conn.execute(
                "UPDATE inventory.spools SET remaining_grams = %s, status = CASE WHEN %s <= 0 THEN 'empty' ELSE status END "
                "WHERE spool_id = %s", (remaining, remaining, s["spool_id"]))
            await conn.execute(
                "INSERT INTO inventory.ledger (spool_id, material, color, delta_grams, reason, ref) VALUES (%s, %s, %s, %s, %s, %s)",
                (s["spool_id"], ev.material, ev.color, -take, reason, ev.plate_id))
            await self.emit(conn, FilamentConsumed(spool_id=s["spool_id"], material=ev.material, color=ev.color,
                                                   grams=round(take, 1), remaining_grams=round(remaining, 1)), cause=env)
            if remaining <= 0:
                await self._raise(conn, env, kind="spool_empty", severity="info", subject=s["spool_id"],
                                  material=ev.material, color=ev.color, spool_id=s["spool_id"], printer_id=ev.printer_id,
                                  message=f"spool {s['spool_id']} ({ev.material} {ev.color}) on {ev.printer_id} is empty: "
                                          "replace it")
            left -= take
        if not spools:
            await conn.execute(
                "INSERT INTO inventory.ledger (spool_id, material, color, delta_grams, reason, ref) VALUES (NULL, %s, %s, %s, %s, %s)",
                (ev.material, ev.color, -ev.grams_used, reason, ev.plate_id))
            await self.emit(conn, FilamentConsumed(spool_id="unknown", material=ev.material, color=ev.color,
                                                   grams=ev.grams_used, remaining_grams=0.0), cause=env)

    # ------------------------------------------------------------ spools (operator commands)

    @on("cmd.inventory.register_spool")
    async def register_spool(self, env, cmd: RegisterSpool, conn):
        remaining = cmd.initial_grams if cmd.remaining_grams is None else min(cmd.remaining_grams, cmd.initial_grams)
        cur = await conn.execute(
            "INSERT INTO inventory.spools (spool_id, material, color, initial_grams, remaining_grams, status, brand, location, "
            "tag, weighed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (cmd.spool_id, cmd.material, cmd.color, cmd.initial_grams, remaining, "stock" if remaining > 0 else "empty",
             cmd.brand, cmd.location or None, cmd.tag or None, None if cmd.remaining_grams is None else datetime.now(UTC)))
        if not cur.rowcount:
            self.log.warning("spool %s already registered", cmd.spool_id)
            return
        await conn.execute(
            "INSERT INTO inventory.ledger (spool_id, material, color, delta_grams, reason, ref) VALUES (%s, %s, %s, %s, "
            "'registered', %s)", (cmd.spool_id, cmd.material, cmd.color, remaining, cmd.registered_by or None))
        await self.emit(conn, SpoolRegistered(spool_id=cmd.spool_id, material=cmd.material, color=cmd.color,
                                              brand=cmd.brand, remaining_grams=remaining), cause=env)
        await self._evaluate(conn, cmd.material, cmd.color, env, reorder=False)

    @on("cmd.inventory.adjust_spool")
    async def adjust_spool(self, env, cmd: AdjustSpool, conn):
        s = await (await conn.execute("SELECT * FROM inventory.spools WHERE spool_id = %s FOR UPDATE",
                                      (cmd.spool_id,))).fetchone()
        if s is None:
            self.log.warning("unknown spool %s", cmd.spool_id)
            return
        delta = cmd.remaining_grams - s["remaining_grams"]
        empty = cmd.remaining_grams <= 0
        await conn.execute(
            "UPDATE inventory.spools SET remaining_grams = %s, weighed_at = now(), "
            "status = CASE WHEN %s THEN 'empty' WHEN status = 'empty' THEN 'stock' ELSE status END WHERE spool_id = %s",
            (cmd.remaining_grams, empty, cmd.spool_id))
        await conn.execute(
            "INSERT INTO inventory.ledger (spool_id, material, color, delta_grams, reason, ref) VALUES (%s, %s, %s, %s, "
            "'adjustment', %s)", (cmd.spool_id, s["material"], s["color"], delta, cmd.reason[:120]))
        await self.emit(conn, SpoolAdjusted(spool_id=cmd.spool_id, material=s["material"], color=s["color"],
                                            delta_grams=round(delta, 1), remaining_grams=cmd.remaining_grams,
                                            reason=cmd.reason, by=cmd.adjusted_by or "unknown"), cause=env)
        if empty:
            await self._raise(conn, env, kind="spool_empty", severity="info", subject=cmd.spool_id, material=s["material"],
                              color=s["color"], spool_id=cmd.spool_id, printer_id=s["printer_id"],
                              message=f"spool {cmd.spool_id} ({s['material']} {s['color']}) is empty")
        else:
            await self._resolve(conn, env, kind="spool_empty", subject=cmd.spool_id)
        await self._evaluate(conn, s["material"], s["color"], env)
        if s["printer_id"]:
            await self._check_printer(conn, s["printer_id"], env)

    @on("cmd.inventory.load_spool")
    async def load_spool(self, env, cmd: LoadSpool, conn):
        spool = await (await conn.execute("SELECT * FROM inventory.spools WHERE spool_id = %s FOR UPDATE",
                                          (cmd.spool_id,))).fetchone()
        if spool is None:
            self.log.warning("unknown spool %s", cmd.spool_id)
            return
        if spool["status"] == "empty" and cmd.printer_id:
            self.log.warning("spool %s is empty; not loaded", cmd.spool_id)
            return
        if cmd.printer_id:
            await conn.execute(
                "UPDATE inventory.spools SET printer_id = %s, slot = %s, status = 'loaded', location = NULL WHERE spool_id = %s",
                (cmd.printer_id, cmd.slot or None, cmd.spool_id),
            )
        else:
            await conn.execute(
                "UPDATE inventory.spools SET printer_id = NULL, slot = NULL, location = %s, "
                "status = CASE WHEN status = 'empty' THEN 'empty' ELSE 'stock' END WHERE spool_id = %s",
                (cmd.location or None, cmd.spool_id))
            if spool["status"] == "empty":  # an empty spool taken off the printer: nothing left to do about it
                await self._resolve(conn, env, kind="spool_empty", subject=cmd.spool_id)
        await self.emit(conn, SpoolLoaded(spool_id=cmd.spool_id, printer_id=cmd.printer_id or None,
                                          material=spool["material"], color=spool["color"]), cause=env)
        for printer in {spool["printer_id"], cmd.printer_id} - {None, ""}:
            await self._check_printer(conn, printer, env)

    @on("cmd.inventory.ack_alert")
    async def ack_alert(self, env, cmd: AcknowledgeInventoryAlert, conn):
        row = await (await conn.execute("SELECT kind, subject FROM inventory.alerts WHERE alert_id = %s AND resolved_at IS NULL",
                                        (cmd.alert_id,))).fetchone()
        if row:
            await self._resolve(conn, env, kind=row["kind"], subject=row["subject"], by=cmd.acknowledged_by or "unknown")

    # ------------------------------------------------------------ reorder rules and purchase orders

    @on("cmd.inventory.set_reorder_rule")
    async def set_reorder_rule(self, env, cmd: SetReorderRule, conn):
        m = await self._material(conn, cmd.material, cmd.color, lock=True)
        if m is None:
            self.log.warning("no material %s/%s", cmd.material, cmd.color)
            return
        new = {"min_stock_grams": cmd.min_stock_grams, "reorder_mode": cmd.mode, "supplier": cmd.supplier,
               "lead_time_days": cmd.lead_time_days}
        new = {k: (m[k] if v is None else v) for k, v in new.items()}
        await conn.execute(
            "UPDATE inventory.materials SET min_stock_grams = %s, reorder_mode = %s, supplier = %s, lead_time_days = %s "
            "WHERE material = %s AND color = %s",
            (new["min_stock_grams"], new["reorder_mode"], new["supplier"], new["lead_time_days"], cmd.material, cmd.color))
        await self.emit(conn, ReorderRuleChanged(material=cmd.material, color=cmd.color, min_stock_grams=new["min_stock_grams"],
                                                 mode=new["reorder_mode"], supplier=new["supplier"],
                                                 lead_time_days=new["lead_time_days"], by=cmd.changed_by or "unknown"),
                        cause=env)
        await self._evaluate(conn, cmd.material, cmd.color, env)

    @on("cmd.inventory.request_reorder")
    async def request_reorder(self, env, cmd: RequestReorder, conn):
        m = await self._material(conn, cmd.material, cmd.color, lock=True)
        if m is None:
            self.log.warning("no material %s/%s", cmd.material, cmd.color)
            return
        pos = await self._position(conn, cmd.material, cmd.color)
        policy = policy_of(m)
        a = assess(policy, pos["on_hand"], pos["on_order_spools"] * m["grams_per_spool"], pos["used_14d"])
        spools = cmd.spools or spools_to_order(policy, pos["on_hand"], pos["on_order_spools"] * m["grams_per_spool"],
                                               a.daily_grams)
        # A person asked for it: no further approval.
        await self._new_po(conn, env, cmd.material, cmd.color, spools, spools * int(m["cost_per_spool_agorot"]),
                           needs_approval=False, why=f"ordered by {cmd.requested_by or 'unknown'}")

    @on("cmd.inventory.update_po")
    async def update_po(self, env, cmd: UpdatePo, conn):
        po = await (await conn.execute(
            "UPDATE inventory.purchase_orders p SET spools = %s, est_cost_agorot = %s * m.cost_per_spool_agorot "
            "FROM inventory.materials m WHERE p.po_id = %s AND p.status = 'pending_approval' "
            "AND m.material = p.material AND m.color = p.color RETURNING p.po_id", (cmd.spools, cmd.spools, cmd.po_id),
        )).fetchone()
        if po is None:
            self.log.warning("PO %s not changed: it is not waiting for approval", cmd.po_id)

    async def _placed(self, conn, po_id, material, color, spools, cost_agorot, cause) -> None:
        # Supplier integration (email / supplier API via an MCP tool server, ADR-0008) hooks in here. Until then a
        # human orders it and confirms with cmd.inventory.confirm_po_ordered.
        await self.emit(conn, ReorderPlaced(po_id=po_id, material=material, color=color, spools=spools,
                                            est_cost_agorot=cost_agorot),
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
            await self._placed(conn, po["po_id"], po["material"], po["color"], po["spools"], po["est_cost_agorot"], env)

    @on("cmd.inventory.confirm_po_ordered")
    async def confirm_po_ordered(self, env, cmd: ConfirmPoOrdered, conn):
        ordered_at = cmd.ordered_at or datetime.now(UTC)
        po = await (
            await conn.execute(
                "UPDATE inventory.purchase_orders SET status = 'ordered', supplier = %s, total_agorot = %s, "
                "vat_invoice = %s, ordered_at = %s WHERE po_id = %s AND status = 'placed' RETURNING *",
                (cmd.supplier, cmd.total_agorot, cmd.vat_invoice, ordered_at, cmd.po_id),
            )
        ).fetchone()
        if po is None:
            row = await (await conn.execute("SELECT status FROM inventory.purchase_orders WHERE po_id = %s",
                                            (cmd.po_id,))).fetchone()
            self.log.warning("PO %s not confirmed as ordered: status is %s", cmd.po_id, row["status"] if row else "unknown")
            return
        m = await self._material(conn, po["material"], po["color"])
        await self.emit(
            conn,
            PurchaseOrdered(po_id=po["po_id"], material=po["material"], color=po["color"], spools=po["spools"],
                            supplier=cmd.supplier, total_agorot=cmd.total_agorot, vat_invoice=cmd.vat_invoice,
                            ordered_at=ordered_at, grams_per_spool=m["grams_per_spool"] if m else None),
            cause=env, correlationid=po["po_id"],
        )

    @on("cmd.inventory.receive_po")
    async def receive_po(self, env, cmd: ReceivePo, conn):
        po = await (
            await conn.execute(
                "UPDATE inventory.purchase_orders SET status = 'received', received_at = now() "
                "WHERE po_id = %s AND status = 'ordered' RETURNING *",
                (cmd.po_id,),
            )
        ).fetchone()
        if po is None:
            self.log.warning("PO %s not received: it is not in status 'ordered'", cmd.po_id)
            return
        m = await self._material(conn, po["material"], po["color"])
        grams = m["grams_per_spool"] if m else 1000
        spool_ids = [f"{po['po_id']}-{i:02d}" for i in range(1, po["spools"] + 1)]
        for sid in spool_ids:
            await conn.execute(
                "INSERT INTO inventory.spools (spool_id, material, color, initial_grams, remaining_grams, status, location, "
                "brand) VALUES (%s, %s, %s, %s, %s, 'stock', %s, %s) ON CONFLICT DO NOTHING",
                (sid, po["material"], po["color"], grams, grams, cmd.location or None, po["supplier"] or ""),
            )
            await conn.execute(
                "INSERT INTO inventory.ledger (spool_id, material, color, delta_grams, reason, ref) "
                "VALUES (%s, %s, %s, %s, 'received', %s)",
                (sid, po["material"], po["color"], grams, po["po_id"]),
            )
        await self.emit(conn, PoReceived(po_id=po["po_id"], material=po["material"], color=po["color"], spool_ids=spool_ids),
                        cause=env, correlationid=po["po_id"])
        await self._evaluate(conn, po["material"], po["color"], env, reorder=False)

    # ------------------------------------------------------------ queries

    async def _availability(self, env):
        q = env.payload()
        async with self.db.tx() as conn:
            row = await (
                await conn.execute(
                    "SELECT (SELECT coalesce(sum(remaining_grams), 0) FROM inventory.spools WHERE material = %(m)s "
                    "AND color = %(c)s AND status <> 'empty') AS on_hand, (SELECT coalesce(sum(grams), 0) FROM "
                    "inventory.reservations WHERE material = %(m)s AND color = %(c)s) AS reserved",
                    {"m": q.material, "c": q.color},
                )
            ).fetchone()
        reply = AvailabilityReply(material=q.material, color=q.color, on_hand_grams=row["on_hand"],
                                  reserved_grams=row["reserved"])
        return new_message(reply, source=self.source, cause=env)
