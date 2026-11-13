"""PrintFarm Ops Ledger service.

Consumes JetStream events and writes ledger rows to Postgres:
  order.payment.recorded / .refunded  -> income_entries (income when PAID)
  inventory.po.ordered                -> expense_entries kind='cash'      (filament purchase = P&L expense)
  print.job.completed                 -> expense_entries kind='allocated' (per-job grams + kWh, margins only)
Answers the command finance.expense.create (manual expense) via request/reply.

Every event must carry a unique "event_id" (or a Nats-Msg-Id header); rows are
idempotent on it, so redelivery never double-books. After MAX_DELIVER failed
attempts a message is copied to dlq.finance.<subject> and acknowledged.
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import asyncpg
import nats
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

from pf_common import subjects as S
from pf_common.nats_setup import ensure_streams

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pf.finance")

DATABASE_URL = os.environ["DATABASE_URL"]
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
MAX_DELIVER = 5
DURABLE = "finance-ledger"
SUBJECTS = [S.ORDER_PAYMENT_RECORDED, S.ORDER_PAYMENT_REFUNDED, S.INVENTORY_PO_ORDERED, S.PRINT_JOB_COMPLETED]


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def split_vat(gross_agorot: int, settings: asyncpg.Record) -> tuple[int, int]:
    """(net, vat) from a gross amount. Osek patur: no VAT."""
    if settings["business_type"] != "murshe":
        return gross_agorot, 0
    rate = Decimal(settings["vat_rate"])
    net = _round(Decimal(gross_agorot) / (1 + rate))
    return net, gross_agorot - net


def _day(value: str | None) -> date:
    return datetime.fromisoformat(value).date() if value else datetime.now(timezone.utc).date()


class Ledger:
    def __init__(self, db: asyncpg.Pool, nc):
        self.db, self.nc = db, nc

    async def settings(self, con) -> asyncpg.Record:
        return await con.fetchrow("SELECT * FROM finance_settings WHERE id = 1")

    async def handle(self, subject: str, event_id: str, data: dict) -> list[int]:
        async with self.db.acquire() as con, con.transaction():
            st = await self.settings(con)
            if subject in (S.ORDER_PAYMENT_RECORDED, S.ORDER_PAYMENT_REFUNDED):
                sign = -1 if subject == S.ORDER_PAYMENT_REFUNDED else 1
                gross = sign * int(data["amount_agorot"])
                net, vat = split_vat(gross, st)
                row = await con.fetchrow(
                    """INSERT INTO income_entries (source_event_id, order_id, entry_date, method, gross_agorot, vat_agorot,
                         net_agorot, receipt_no, product_type, source)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'auto') ON CONFLICT (source_event_id) DO NOTHING RETURNING id""",
                    event_id, data.get("order_id"), _day(data.get("paid_at")), data.get("method", "unknown"),
                    gross, vat, net, data.get("receipt_no"), data.get("product_type"))
                return [row["id"]] if row else []

            if subject == S.INVENTORY_PO_ORDERED:
                gross = int(data["total_agorot"])
                net, vat = split_vat(gross, st) if data.get("vat_invoice", True) else (gross, 0)
                row = await con.fetchrow(
                    """INSERT INTO expense_entries (source_event_id, entry_date, category, vendor, gross_agorot, vat_agorot,
                         net_agorot, kind, note)
                       VALUES ($1,$2,'filament',$3,$4,$5,$6,'cash',$7) ON CONFLICT (source_event_id) DO NOTHING RETURNING id""",
                    event_id, _day(data.get("ordered_at")), data.get("supplier"), gross, vat, net, data.get("po_id"))
                return [row["id"]] if row else []

            if subject == S.PRINT_JOB_COMPLETED:
                ids = []
                grams = Decimal(str(data.get("grams_used", 0)))           # from G-code metadata
                price_per_g = Decimal(str(data.get("spool_price_per_gram_agorot", st["default_filament_agorot_per_gram"])))
                hours = Decimal(str(data.get("print_seconds", 0))) / 3600
                watts = Decimal(str(data.get("printer_avg_watts", st["default_printer_watts"])))
                kwh = hours * watts / 1000
                for cat, amount, note in (
                    ("filament", _round(grams * price_per_g), f"{grams} g"),
                    ("electricity", _round(kwh * Decimal(st["electricity_agorot_per_kwh"])), f"{kwh:.3f} kWh"),
                ):
                    row = await con.fetchrow(
                        """INSERT INTO expense_entries (source_event_id, entry_date, category, gross_agorot, vat_agorot,
                             net_agorot, kind, job_id, product_type, note)
                           VALUES ($1,$2,$3,$4,0,$4,'allocated',$5,$6,$7) ON CONFLICT (source_event_id) DO NOTHING RETURNING id""",
                        f"{event_id}:{cat}", _day(data.get("completed_at")), cat, amount, data.get("job_id"),
                        data.get("product_type"), note)
                    if row:
                        ids.append(row["id"])
                return ids
        return []

    async def create_manual_expense(self, data: dict) -> int:
        async with self.db.acquire() as con:
            st = await self.settings(con)
            gross = int(data["gross_agorot"])
            net, vat = split_vat(gross, st) if data.get("vat_invoice") else (gross, 0)
            return await con.fetchval(
                """INSERT INTO expense_entries (source_event_id, entry_date, category, vendor, gross_agorot, vat_agorot,
                     net_agorot, kind, note) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (source_event_id) DO UPDATE SET note = expense_entries.note RETURNING id""",
                data["command_id"], _day(data.get("date")), data["category"], data.get("vendor"), gross, vat, net,
                "estimated" if data.get("estimated") else "cash", data.get("note"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    nc = await nats.connect(NATS_URL, name="finance", max_reconnect_attempts=-1)
    js = nc.jetstream()
    await ensure_streams(js)
    ledger = Ledger(db, nc)

    async def on_event(msg):
        meta = msg.metadata
        event_id = (msg.headers or {}).get("Nats-Msg-Id")
        try:
            data = json.loads(msg.data)
            event_id = data.get("event_id") or event_id or f"{meta.stream}:{meta.sequence.stream}"
            ids = await ledger.handle(msg.subject, event_id, data)
            for i in ids:
                await js.publish(S.FINANCE_ENTRY_CREATED, json.dumps({"entry_id": i, "source": msg.subject,
                                                                       "event_id": event_id}).encode())
            if ids:
                await js.publish(S.FINANCE_SUMMARY_UPDATED, b"{}")
            await msg.ack()
        except Exception as exc:  # noqa: BLE001
            log.exception("failed %s (delivery %s)", msg.subject, meta.num_delivered)
            if meta.num_delivered >= MAX_DELIVER:
                await js.publish(f"dlq.finance.{msg.subject}", msg.data,
                                 headers={"PF-Error": str(exc)[:500], "PF-Subject": msg.subject})
                await msg.ack()
            else:
                await msg.nak(delay=2 ** meta.num_delivered)

    cfg = ConsumerConfig(durable_name=DURABLE, filter_subjects=SUBJECTS, ack_policy=AckPolicy.EXPLICIT,
                         deliver_policy=DeliverPolicy.ALL, max_deliver=MAX_DELIVER + 1, ack_wait=30)
    sub = await js.pull_subscribe_bind(DURABLE, stream="EVENTS") if await _consumer_exists(js) else \
        await js.pull_subscribe("", durable=DURABLE, stream="EVENTS", config=cfg)

    async def loop():
        while True:
            try:
                for m in await sub.fetch(20, timeout=5):
                    await on_event(m)
            except nats.errors.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                log.exception("consumer loop error; retrying")
                await asyncio.sleep(2)

    async def on_expense_cmd(msg):
        try:
            body = json.loads(msg.data)
            entry_id = await ledger.create_manual_expense({**body["data"], "command_id": body["command_id"]})
            await js.publish(S.FINANCE_ENTRY_CREATED, json.dumps({"entry_id": entry_id, "source": "manual"}).encode())
            await js.publish(S.FINANCE_SUMMARY_UPDATED, b"{}")
            await msg.respond(json.dumps({"ok": True, "entry_id": entry_id}).encode())
        except Exception as exc:  # noqa: BLE001
            log.exception("expense command failed")
            await msg.respond(json.dumps({"ok": False, "error": str(exc)[:300]}).encode())

    await nc.subscribe(S.COMMANDS["expenses.create"], queue="finance", cb=on_expense_cmd)
    task = asyncio.create_task(loop())
    app.state.db, app.state.nc = db, nc
    log.info("finance ledger ready, consuming %s", SUBJECTS)
    yield
    task.cancel()
    await nc.drain()
    await db.close()


async def _consumer_exists(js) -> bool:
    try:
        await js.consumer_info("EVENTS", DURABLE)
        return True
    except nats.js.errors.NotFoundError:
        return False


app = FastAPI(title="PrintFarm Ledger", lifespan=lifespan)


@app.get("/health")
async def health():
    ok = app.state.nc.is_connected
    try:
        await app.state.db.fetchval("SELECT 1")
    except Exception:  # noqa: BLE001
        ok = False
    return JSONResponse({"ok": ok}, status_code=200 if ok else 503)
