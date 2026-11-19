"""Finance read side: profit & loss for a period (vs the previous one), the 12-month trend, product margins,
receivables, VAT, the income and expense ledgers, settings and CSV export. The rules are in
printfarm.agents.finance.reports (pure); changes go through commands (payments.record, expenses.create,
finance.settings)."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from printfarm.agents.finance.reports import (
    ExpenseRow,
    Period,
    change_pct,
    period_of,
    pnl,
    previous,
    product_margins,
    vat_summary,
)
from printfarm.contracts.catalog import STOCK_CHANNEL

from . import auth

router = APIRouter(prefix="/api/v1/finance", dependencies=[Depends(auth.require_auth)])
PLACEHOLDERS = {"vat_rate": 0.18, "electricity_agorot_per_kwh": 64, "default_printer_watts": 150,
                "default_filament_agorot_per_gram": 8.5}


def _anchor(month: str | None) -> date:
    try:
        return date.fromisoformat(f"{month}-01") if month else date.today().replace(day=1)
    except ValueError:
        raise HTTPException(400, "month must look like 2026-11") from None


def _p(p: Period) -> dict:
    return {"kind": p.kind, "start": p.start.isoformat(), "end": p.end.isoformat(), "label": p.label}


async def _pnl(db, p: Period):
    inc = await db.fetchrow(
        "SELECT coalesce(sum(net_agorot), 0) AS net, coalesce(sum(vat_agorot), 0) AS vat FROM finance.income_entries "
        "WHERE entry_date >= %s AND entry_date < %s", (p.start, p.end)) or {}
    rows = await db.fetchall(
        "SELECT category, kind, coalesce(sum(net_agorot), 0) AS net, coalesce(sum(vat_agorot), 0) AS vat "
        "FROM finance.expense_entries WHERE entry_date >= %s AND entry_date < %s GROUP BY category, kind", (p.start, p.end))
    exp = [ExpenseRow(r["category"], r["kind"], int(r["net"]), int(r["vat"])) for r in rows]
    return pnl(int(inc.get("net") or 0), exp), int(inc.get("vat") or 0), exp


def _pnl_json(r) -> dict:
    return {"revenue_agorot": r.revenue, "expenses_agorot": r.expenses, "profit_agorot": r.profit,
            "margin_pct": r.margin_pct, "mix": r.mix, "electricity_agorot": r.electricity,
            "electricity_estimated": r.electricity_estimated, "electricity_from_jobs": r.electricity_from_jobs}


async def _receivables(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT o.order_id, o.customer_name, o.total_agorot, "
        "(SELECT coalesce(sum(CASE p.kind WHEN 'payment' THEN p.amount_agorot ELSE -p.amount_agorot END), 0) "
        "   FROM coordinator.payments p WHERE p.order_id = o.order_id) AS paid_agorot, "
        "(SELECT max(h.at) FROM coordinator.saga_history h WHERE h.saga_id = o.order_id AND h.to_state = 'SHIPPED') "
        "   AS shipped_at "
        "FROM coordinator.orders o JOIN coordinator.sagas s ON s.saga_id = o.order_id "
        "WHERE s.state = 'SHIPPED' AND o.total_agorot > 0 ORDER BY shipped_at")
    out = []
    for r in rows:
        due = int(r["total_agorot"]) - int(r["paid_agorot"])
        if due > 0:
            out.append({**r, "due_agorot": due})
    return out


@router.get("/report")
async def report(request: Request, period: str = Query("month", pattern="^(month|quarter|year)$"), month: str | None = None):
    db = request.app.state.db
    cur = period_of(period, _anchor(month))
    prev = previous(cur)
    now, _out_vat, _ = await _pnl(db, cur)
    before, _, _ = await _pnl(db, prev)
    hours = await db.fetchval(
        "SELECT coalesce(sum(extract(epoch FROM finished_at - started_at)) / 3600, 0) FROM fleet.plates "
        "WHERE finished_at >= %s AND finished_at < %s AND started_at IS NOT NULL", (cur.start, cur.end))
    receivables = await _receivables(db)
    return {
        "period": _p(cur), "previous": _p(prev), "current": _pnl_json(now), "before": _pnl_json(before),
        "change_pct": {"revenue": change_pct(now.revenue, before.revenue), "expenses": change_pct(now.expenses, before.expenses),
                       "profit": change_pct(now.profit, before.profit)},
        "margin_change_pt": round(now.margin_pct - before.margin_pct, 1)
        if now.margin_pct is not None and before.margin_pct is not None else None,
        "print_hours": round(float(hours or 0), 1),
        "receivables": {"total_agorot": sum(r["due_agorot"] for r in receivables), "orders": len(receivables)},
    }


@router.get("/trend")
async def trend(request: Request, months: int = Query(12, ge=1, le=36), month: str | None = None):
    """Revenue, expenses and profit per month (oldest first), with the same electricity rule as the report."""
    db, last = request.app.state.db, period_of("month", _anchor(month))
    periods = [last]
    for _ in range(months - 1):
        periods.insert(0, previous(periods[0]))
    out = []
    for p in periods:
        r, _, _ = await _pnl(db, p)
        out.append({"month": p.start.isoformat()[:7], "label": p.label, "revenue_agorot": r.revenue,
                    "expenses_agorot": r.expenses, "profit_agorot": r.profit})
    return {"months": out}


@router.get("/products")
async def products(request: Request, period: str = Query("month", pattern="^(month|quarter|year)$"), month: str | None = None):
    """Margin per product type: revenue from payments, filament and electricity allocated per job, units shipped."""
    db, p = request.app.state.db, period_of(period, _anchor(month))
    revenue = {r["product"]: int(r["net"]) for r in await db.fetchall(
        "SELECT coalesce(product_type, 'other') AS product, sum(net_agorot) AS net FROM finance.income_entries "
        "WHERE entry_date >= %s AND entry_date < %s GROUP BY 1", (p.start, p.end))}
    rows = await db.fetchall(
        "SELECT coalesce(e.product_type, 'other') AS product, e.category, coalesce(o.channel = %s, false) AS stock, "
        "sum(e.net_agorot) AS net FROM finance.expense_entries e "
        "LEFT JOIN fleet.jobs j ON j.job_id = e.job_id LEFT JOIN coordinator.orders o ON o.order_id = j.order_id "
        "WHERE e.kind = 'allocated' AND e.entry_date >= %s AND e.entry_date < %s GROUP BY 1, 2, 3",
        (STOCK_CHANNEL, p.start, p.end))
    allocated = [(r["product"], r["category"], int(r["net"])) for r in rows if not r["stock"]]
    stock_build: dict[str, int] = {}
    for r in rows:
        if r["stock"]:
            stock_build[r["product"]] = stock_build.get(r["product"], 0) + int(r["net"])
    # Units shipped to customers, per product type: the SKU for catalog lines (as revenue and costs are keyed), the
    # product name otherwise. Stock batches "ship" to the shelf, so they are not sales.
    units = {r["product"]: int(r["n"]) for r in await db.fetchall(
        "SELECT CASE WHEN i.product = 'catalog' AND i.sku IS NOT NULL THEN i.sku ELSE i.product END AS product, "
        "sum(i.quantity) AS n FROM coordinator.order_items i "
        "JOIN coordinator.orders o ON o.order_id = i.order_id AND o.channel <> %s "
        "JOIN coordinator.saga_history h ON h.saga_id = i.order_id AND h.to_state = 'SHIPPED' "
        "WHERE h.at >= %s AND h.at < %s GROUP BY 1", (STOCK_CHANNEL, p.start, p.end))}
    return {"period": _p(p), "products": [
        {"product_type": m.product_type, "units": m.units, "revenue_agorot": m.revenue, "costs": m.costs,
         "margin_agorot": m.margin, "margin_pct": m.margin_pct, "stock_build_agorot": m.stock_build}
        for m in product_margins(revenue, allocated, units, stock_build)]}


@router.get("/receivables")
async def receivables(request: Request):
    """Shipped orders not (fully) paid. Not income until a payment is recorded."""
    rows = await _receivables(request.app.state.db)
    return {"total_agorot": sum(r["due_agorot"] for r in rows), "orders": rows}


@router.get("/vat")
async def vat(request: Request, month: str | None = None):
    """VAT per two-month return period of the year (output on income, input on expenses with a tax invoice)."""
    db, anchor = request.app.state.db, _anchor(month)
    s = await db.fetchrow("SELECT business_type, vat_rate, turnover_ceiling_agorot FROM finance.settings WHERE id = 1") or {}
    out = []
    for m in range(1, 13, 2):
        p = period_of("vat", date(anchor.year, m, 1))
        row = await db.fetchrow(
            "SELECT (SELECT coalesce(sum(vat_agorot), 0) FROM finance.income_entries WHERE entry_date >= %(a)s "
            "AND entry_date < %(b)s) AS output, (SELECT coalesce(sum(vat_agorot), 0) FROM finance.expense_entries "
            "WHERE kind IN ('cash', 'estimated') AND entry_date >= %(a)s AND entry_date < %(b)s) AS input, "
            "(SELECT coalesce(sum(gross_agorot), 0) FROM finance.income_entries WHERE entry_date >= %(a)s "
            "AND entry_date < %(b)s) AS turnover", {"a": p.start, "b": p.end}) or {}
        v = vat_summary(int(row.get("output") or 0), int(row.get("input") or 0))
        out.append({"period": _p(p), "output_vat_agorot": v.output_vat, "input_vat_agorot": v.input_vat,
                    "payable_agorot": v.payable, "turnover_gross_agorot": int(row.get("turnover") or 0)})
    turnover = sum(x["turnover_gross_agorot"] for x in out)
    return {"year": anchor.year, "business_type": s.get("business_type"), "periods": out,
            "current": _p(period_of("vat", anchor)), "turnover_gross_agorot": turnover,
            "turnover_ceiling_agorot": s.get("turnover_ceiling_agorot")}


INCOME_SQL = ("SELECT i.entry_date, i.order_id, o.customer_name, i.method, i.gross_agorot, i.vat_agorot, i.net_agorot, "
              "i.receipt_no, i.product_type, i.source FROM finance.income_entries i "
              "LEFT JOIN coordinator.orders o ON o.order_id = i.order_id "
              "WHERE i.entry_date >= %s AND i.entry_date < %s ORDER BY i.entry_date DESC, i.id DESC")
EXPENSE_SQL = ("SELECT entry_date, category, vendor, kind, gross_agorot, vat_agorot, net_agorot, job_id, product_type, note "
               "FROM finance.expense_entries WHERE entry_date >= %s AND entry_date < %s "
               "AND (%s OR kind <> 'allocated') ORDER BY entry_date DESC, id DESC")


@router.get("/income")
async def income(request: Request, period: str = Query("month", pattern="^(month|quarter|year)$"), month: str | None = None):
    p = period_of(period, _anchor(month))
    return {"period": _p(p), "entries": await request.app.state.db.fetchall(INCOME_SQL, (p.start, p.end))}


@router.get("/expenses")
async def expenses(request: Request, period: str = Query("month", pattern="^(month|quarter|year)$"), month: str | None = None,
                   allocated: bool = True):
    p = period_of(period, _anchor(month))
    return {"period": _p(p), "entries": await request.app.state.db.fetchall(EXPENSE_SQL, (p.start, p.end, allocated))}


@router.get("/export.csv")
async def export(request: Request, ledger: str = Query(..., pattern="^(income|expenses)$"),
                 period: str = Query("month", pattern="^(month|quarter|year)$"), month: str | None = None):
    """The ledger of a period as CSV (amounts in shekels), for the accountant."""
    p = period_of(period, _anchor(month))
    db = request.app.state.db
    rows = await (db.fetchall(INCOME_SQL, (p.start, p.end)) if ledger == "income"
                  else db.fetchall(EXPENSE_SQL, (p.start, p.end, True)))
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=[k.replace("_agorot", "_ils") for k in rows[0]])
        w.writeheader()
        for r in rows:
            w.writerow({k.replace("_agorot", "_ils"): f"{v / 100:.2f}" if k.endswith("_agorot") and v is not None else v
                        for k, v in r.items()})
    name = f"printfarm-{ledger}-{p.start.isoformat()[:7]}-{p.kind}.csv"
    # UTF-8 with a BOM so Excel shows Hebrew names correctly
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/settings")
async def settings(request: Request):
    row = await request.app.state.db.fetchrow("SELECT * FROM finance.settings WHERE id = 1") or {}
    out = {k: (float(v) if k in PLACEHOLDERS and v is not None else v) for k, v in row.items() if k != "id"}
    out["placeholders"] = [k for k, v in PLACEHOLDERS.items() if out.get(k) == v] if not row.get("confirmed_at") else []
    out["confirmed"] = bool(row.get("confirmed_at"))
    out["filament_prices"] = await request.app.state.db.fetchall(
        "SELECT material, color, agorot_per_gram::float AS agorot_per_gram, po_id, at FROM finance.filament_prices "
        "ORDER BY material, color")
    return out


@router.get("/summary")
async def summary(request: Request, month: str | None = None):
    """One month's profit and loss (kept for scripts; the dashboard uses /report)."""
    p = period_of("month", _anchor(month))
    r, _, rows = await _pnl(request.app.state.db, p)
    allocated = sum(x.net for x in rows if x.kind == "allocated")
    return {"month": p.start.isoformat()[:7], "revenue_net_agorot": r.revenue, "expenses_net_agorot": r.expenses,
            "profit_net_agorot": r.profit, "margin_pct": r.margin_pct, "allocated_cost_net_agorot": allocated,
            "electricity_estimated": r.electricity_estimated,
            "note": "allocated = per-job filament/electricity; used for product margins, excluded from profit"}
