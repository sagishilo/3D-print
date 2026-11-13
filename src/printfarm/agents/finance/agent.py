"""Finance Agent (ledger): books income and expenses from domain events, in Postgres schema ``finance``.

    evt.order.payment.recorded / .refunded  -> finance.income_entries  (income when PAID, VAT split)
    evt.inventory.po.ordered                -> finance.expense_entries kind='cash'      (filament purchase, P&L)
    evt.fleet.plate.finished                -> finance.expense_entries kind='allocated' (per-job grams + kWh, margins only)
    cmd.finance.create_expense              -> finance.expense_entries kind='cash' | 'estimated'

Exactly-once booking comes from two layers: the agent inbox (per consumer) and a UNIQUE source_event_id on every
row (per bus message), which also protects against re-reading history under a new consumer name.
"""

from __future__ import annotations

from printfarm.agents.base import Agent, on
from printfarm.agents.finance.ledger import FinanceSettings, business_day, plate_allocations, split_vat
from printfarm.contracts.messages import (
    CreateExpense,
    LedgerEntryCreated,
    PaymentRecorded,
    PaymentRefunded,
    PlateFinished,
    PurchaseOrdered,
)


class FinanceAgent(Agent):
    name = "finance"

    async def _settings(self, conn) -> FinanceSettings:
        row = await (await conn.execute("SELECT * FROM finance.settings WHERE id = 1")).fetchone()
        return FinanceSettings.from_row(row) if row else FinanceSettings()

    def _day(self, when):
        return business_day(when, self.settings.business_tz)

    async def _booked(self, conn, env, *, entry_id: int, ledger: str, entry_kind: str, category: str | None,
                      gross: int, source_event_id: str) -> None:
        await self.emit(conn, LedgerEntryCreated(entry_id=entry_id, ledger=ledger, entry_kind=entry_kind, category=category,
                                                 gross_agorot=gross, source_event_id=source_event_id), cause=env)

    # ------------------------------------------------------------ income

    @on("evt.order.payment.>", from_start=True)
    async def payment(self, env, ev: PaymentRecorded | PaymentRefunded, conn):
        s = await self._settings(conn)
        refund = isinstance(ev, PaymentRefunded)
        gross = -ev.amount_agorot if refund else ev.amount_agorot
        net, vat = split_vat(gross, s)
        row = await (
            await conn.execute(
                "INSERT INTO finance.income_entries (source_event_id, order_id, entry_date, method, gross_agorot, vat_agorot, "
                "net_agorot, receipt_no, product_type, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source_event_id) DO NOTHING RETURNING id",
                (env.id, ev.order_id, self._day(ev.refunded_at if refund else ev.paid_at), ev.method.value, gross, vat, net,
                 ev.receipt_no, ev.product_type, "auto" if ev.order_id else "manual"),
            )
        ).fetchone()
        if row:
            await self._booked(conn, env, entry_id=row["id"], ledger="income", entry_kind="auto" if ev.order_id else "manual",
                               category=None, gross=gross, source_event_id=env.id)

    # ------------------------------------------------------------ expenses

    @on("evt.inventory.po.ordered", from_start=True)
    async def po_ordered(self, env, ev: PurchaseOrdered, conn):
        s = await self._settings(conn)
        net, vat = split_vat(ev.total_agorot, s) if ev.vat_invoice else (ev.total_agorot, 0)
        row = await (
            await conn.execute(
                "INSERT INTO finance.expense_entries (source_event_id, entry_date, category, vendor, gross_agorot, vat_agorot, "
                "net_agorot, kind, note) VALUES (%s, %s, 'filament', %s, %s, %s, %s, 'cash', %s) "
                "ON CONFLICT (source_event_id) DO NOTHING RETURNING id",
                (env.id, self._day(ev.ordered_at), ev.supplier, ev.total_agorot, vat, net,
                 f"{ev.po_id} · {ev.spools} x {ev.material} {ev.color}"),
            )
        ).fetchone()
        if row:
            await self._booked(conn, env, entry_id=row["id"], ledger="expense", entry_kind="cash", category="filament",
                               gross=ev.total_agorot, source_event_id=env.id)

    @on("evt.fleet.plate.finished", from_start=True)
    async def plate_finished(self, env, ev: PlateFinished, conn):
        s = await self._settings(conn)
        allocations = plate_allocations(
            grams_used=ev.grams_used, print_seconds=ev.print_seconds, shares=ev.job_shares, material=ev.material,
            color=ev.color, succeeded=ev.succeeded, s=s,
        )
        day = self._day(env.time)
        for a in allocations:
            source_id = f"{env.id}:{a.job_id or 'plate'}:{a.category}"
            row = await (
                await conn.execute(
                    "INSERT INTO finance.expense_entries (source_event_id, entry_date, category, gross_agorot, vat_agorot, "
                    "net_agorot, kind, job_id, product_type, note) VALUES (%s, %s, %s, %s, 0, %s, 'allocated', %s, %s, %s) "
                    "ON CONFLICT (source_event_id) DO NOTHING RETURNING id",
                    (source_id, day, a.category, a.amount_agorot, a.amount_agorot, a.job_id, a.product_type,
                     f"{ev.plate_id} · {a.note}"),
                )
            ).fetchone()
            if row:
                await self._booked(conn, env, entry_id=row["id"], ledger="expense", entry_kind="allocated",
                                   category=a.category, gross=a.amount_agorot, source_event_id=source_id)

    @on("cmd.finance.create_expense")
    async def create_expense(self, env, cmd: CreateExpense, conn):
        s = await self._settings(conn)
        net, vat = split_vat(cmd.gross_agorot, s) if cmd.vat_invoice else (cmd.gross_agorot, 0)
        kind = "estimated" if cmd.estimated else "cash"
        row = await (
            await conn.execute(
                "INSERT INTO finance.expense_entries (source_event_id, entry_date, category, vendor, gross_agorot, vat_agorot, "
                "net_agorot, kind, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source_event_id) DO NOTHING RETURNING id",
                (env.id, self._day(cmd.date), cmd.category.value, cmd.vendor, cmd.gross_agorot, vat, net, kind, cmd.note),
            )
        ).fetchone()
        if row:
            await self._booked(conn, env, entry_id=row["id"], ledger="expense", entry_kind=kind, category=cmd.category.value,
                               gross=cmd.gross_agorot, source_event_id=env.id)
