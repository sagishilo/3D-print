"""Finance Agent (ledger): books income and expenses from domain events, in Postgres schema ``finance``.

    evt.order.payment.recorded / .refunded  -> finance.income_entries  (income when PAID, VAT split)
                                               + finance.income_lines (split across the order's lines, per niche)
    evt.inventory.po.ordered                -> finance.expense_entries kind='cash'      (filament purchase, P&L)
    evt.fleet.plate.finished                -> finance.expense_entries kind='allocated' (per-job grams + kWh, margins only)
    cmd.finance.create_expense              -> finance.expense_entries kind='cash' | 'estimated'
    cmd.finance.update_settings             -> finance.settings (VAT, business type, tariffs; confirmed by whom)
Per-job filament is priced from the latest purchase order of that filament (finance.filament_prices), the
settings' default only for a filament never bought through a PO.

Exactly-once booking comes from two layers: the agent inbox (per consumer) and a UNIQUE source_event_id on every
row (per bus message), which also protects against re-reading history under a new consumer name.
"""

from __future__ import annotations

from decimal import Decimal

from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.finance.ledger import FinanceSettings, business_day, plate_allocations, split_income, split_vat
from printfarm.contracts.messages import (
    CreateExpense,
    FinanceSettingsChanged,
    LedgerEntryCreated,
    PaymentRecorded,
    PaymentRefunded,
    PlateFinished,
    PurchaseOrdered,
    UpdateFinanceSettings,
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
            # Split across the order's lines, so revenue per SKU and per niche adds up to the ledger (no "mixed").
            for line in split_income(net, ev.lines, product_type=ev.product_type):
                await conn.execute(
                    "INSERT INTO finance.income_lines (entry_id, line_no, order_id, product_type, niche, net_agorot) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (row["id"], line.line_no, ev.order_id, line.product_type, line.niche, line.net_agorot))
            await self._booked(conn, env, entry_id=row["id"], ledger="income", entry_kind="auto" if ev.order_id else "manual",
                               category=None, gross=gross, source_event_id=env.id)

    # ------------------------------------------------------------ expenses

    @on("evt.inventory.po.ordered", from_start=True)
    async def po_ordered(self, env, ev: PurchaseOrdered, conn):
        s = await self._settings(conn)
        net, vat = split_vat(ev.total_agorot, s) if ev.vat_invoice else (ev.total_agorot, 0)
        # Filament, or (Wave 1 / M4) a part / consumable ("parts") or packaging bought for the bill of materials.
        category = "filament" if not ev.item_id else "packaging" if ev.item_kind == "packaging" else "parts"
        note = (f"{ev.po_id} · {ev.spools} x {ev.material} {ev.color}" if not ev.item_id
                else f"{ev.po_id} · {ev.quantity or 0:g} x {ev.material}")
        row = await (
            await conn.execute(
                "INSERT INTO finance.expense_entries (source_event_id, entry_date, category, vendor, gross_agorot, vat_agorot, "
                "net_agorot, kind, note) VALUES (%s, %s, %s, %s, %s, %s, %s, 'cash', %s) "
                "ON CONFLICT (source_event_id) DO NOTHING RETURNING id",
                (env.id, self._day(ev.ordered_at), category, ev.supplier, ev.total_agorot, vat, net, note),
            )
        ).fetchone()
        if row:
            await self._booked(conn, env, entry_id=row["id"], ledger="expense", entry_kind="cash", category=category,
                               gross=ev.total_agorot, source_event_id=env.id)
        if ev.grams_per_spool and ev.spools > 0:
            # What a gram of this filament really cost (net: reclaimable VAT is not a cost), for per-job costs.
            await conn.execute(
                "INSERT INTO finance.filament_prices (material, color, agorot_per_gram, po_id, at) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (material, color) DO UPDATE SET agorot_per_gram = EXCLUDED.agorot_per_gram, "
                "po_id = EXCLUDED.po_id, at = EXCLUDED.at WHERE finance.filament_prices.at <= EXCLUDED.at",
                (ev.material, ev.color, Decimal(net) / Decimal(str(ev.spools * ev.grams_per_spool)), ev.po_id,
                 ev.ordered_at),
            )

    @on("evt.fleet.plate.finished", from_start=True)
    async def plate_finished(self, env, ev: PlateFinished, conn):
        s = await self._settings(conn)
        price = await (await conn.execute(
            "SELECT agorot_per_gram FROM finance.filament_prices WHERE material = %s AND color = %s",
            (ev.material, ev.color))).fetchone()
        allocations = plate_allocations(
            grams_used=ev.grams_used, print_seconds=ev.print_seconds, shares=ev.job_shares, material=ev.material,
            color=ev.color, succeeded=ev.succeeded, s=s,
            printer_watts=Decimal(str(ev.printer_watts)) if ev.printer_watts else None,
            price_per_gram_agorot=Decimal(price["agorot_per_gram"]) if price else None,
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

    @on("cmd.finance.update_settings")
    async def update_settings(self, env, cmd: UpdateFinanceSettings, conn):
        who = cmd.updated_by or "unknown"
        await conn.execute(
            "UPDATE finance.settings SET business_type = %s, vat_rate = %s, electricity_agorot_per_kwh = %s, "
            "default_printer_watts = %s, default_filament_agorot_per_gram = %s, turnover_ceiling_agorot = %s, "
            "updated_by = %s, updated_at = now(), "
            "confirmed_by = CASE WHEN %s THEN %s ELSE confirmed_by END, "
            "confirmed_at = CASE WHEN %s THEN now() ELSE confirmed_at END WHERE id = 1",
            (cmd.business_type, Decimal(str(cmd.vat_rate)), Decimal(str(cmd.electricity_agorot_per_kwh)),
             Decimal(str(cmd.default_printer_watts)), Decimal(str(cmd.default_filament_agorot_per_gram)),
             cmd.turnover_ceiling_agorot, who, cmd.confirm, who, cmd.confirm),
        )
        if cmd.labour_agorot_per_hour is not None:
            await conn.execute("UPDATE finance.settings SET labour_agorot_per_hour = %s WHERE id = 1",
                               (Decimal(str(cmd.labour_agorot_per_hour)),))
        if cmd.channel_fees is not None:
            await conn.execute("UPDATE finance.settings SET channel_fees = %s WHERE id = 1",
                               (Jsonb({k: v for k, v in cmd.channel_fees.items() if v > 0}),))
        self.log.warning("finance settings changed by %s (%s, VAT %.2f%%)%s", who, cmd.business_type, cmd.vat_rate * 100,
                         " and confirmed" if cmd.confirm else "")
        await self.emit(conn, FinanceSettingsChanged(business_type=cmd.business_type, vat_rate=cmd.vat_rate,
                                                     confirmed=cmd.confirm, by=who), cause=env)

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
