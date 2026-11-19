"""Analytics read side (Wave 1 / M6): the daily roll-up, the niche scorecard and the Trend Analyst's signals.

GET /api/v1/analytics/daily       days of reporting.daily_niche (or ?by=sku: daily_sku), ?format=csv|xlsx
GET /api/v1/analytics/scorecard   niches side by side over a period against the period before: revenue, profit,
                                  profit per print hour, units, QC failures, demand trend, rising SKUs, the
                                  nightly insight and suggestions (queue stock, raise a price, check quality)
GET /api/v1/trend/signals         the latest signals (and insights), for the Social page's Trends panel
The tables are written by the trend agent (hourly); cmd.trend.backfill (analytics.backfill) rebuilds them.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request

from printfarm.agents.finance.costing import unit_economics
from printfarm.contracts.catalog import trend_pct

from . import auth, export, pricing
from .niches import niche_scope

router = APIRouter()
MONEY = [Depends(auth.OPERATOR)]               # revenue and profit: not for viewers
TZ = ZoneInfo("Asia/Jerusalem")
QC_ALERT_PCT = 10.0


def business_today() -> date:
    return datetime.now(UTC).astimezone(TZ).date()


DAILY_COLUMNS = ["day", "niche", "units_sold", "revenue_agorot", "cost_agorot", "print_hours", "qc_checked", "qc_failed",
                 "captures", "leads", "posted"]


@router.get("/api/v1/analytics/daily", dependencies=MONEY)
async def daily(request: Request, days: int = Query(30, ge=1, le=400), niche: str | None = None,
                by: str = Query("niche", pattern="^(niche|sku)$"), format: str | None = Query(None, pattern=export.FORMAT)):
    scope, end = niche_scope(niche), business_today()
    table = "reporting.daily_sku" if by == "sku" else "reporting.daily_niche"
    cols = ["day", *(["sku"] if by == "sku" else []), *DAILY_COLUMNS[1:]]
    rows = await request.app.state.db.fetchall(
        f"SELECT {', '.join(c if c != 'print_hours' else 'print_hours::float AS print_hours' for c in cols)} "
        f"FROM {table} WHERE day > %s AND day <= %s"
        + (" AND niche = %s" if scope else "") + " ORDER BY day, niche" + (", sku" if by == "sku" else ""),
        (end - timedelta(days=days), end, *((scope,) if scope else ())))
    if format:
        return export.table(rows, cols, name=f"printfarm-daily-{by}-{export.stamp(end)}", fmt=format, sheet="Daily")
    last = await request.app.state.db.fetchval("SELECT max(rolled_at) FROM reporting.rollups")
    return {"days": days, "by": by, "rows": rows, "rolled_at": last}


SCORE_SQL = """
SELECT niche,
  coalesce(sum(revenue_agorot) FILTER (WHERE day > %(cur)s), 0) AS revenue,
  coalesce(sum(cost_agorot) FILTER (WHERE day > %(cur)s), 0) AS cost,
  coalesce(sum(units_sold) FILTER (WHERE day > %(cur)s), 0) AS units,
  coalesce(sum(print_hours) FILTER (WHERE day > %(cur)s), 0)::float AS hours,
  coalesce(sum(qc_checked) FILTER (WHERE day > %(cur)s), 0) AS qc_checked,
  coalesce(sum(qc_failed) FILTER (WHERE day > %(cur)s), 0) AS qc_failed,
  coalesce(sum(leads) FILTER (WHERE day > %(cur)s), 0) AS leads,
  coalesce(sum(revenue_agorot) FILTER (WHERE day <= %(cur)s), 0) AS revenue_prev,
  coalesce(sum(cost_agorot) FILTER (WHERE day <= %(cur)s), 0) AS cost_prev,
  coalesce(sum(units_sold) FILTER (WHERE day <= %(cur)s), 0) AS units_prev,
  coalesce(sum(leads) FILTER (WHERE day <= %(cur)s), 0) AS leads_prev
FROM reporting.daily_niche WHERE day > %(prev)s AND day <= %(end)s GROUP BY niche
"""

SCORE_COLUMNS = ["name", "revenue_agorot", "cost_agorot", "profit_agorot", "margin_pct", "target_margin_pct", "units",
                 "units_prev", "demand_pct", "print_hours", "profit_per_print_hour_agorot", "qc_fail_pct", "leads",
                 "rising", "insight"]


async def _below_target(db, ctx: dict) -> dict[str, list[dict]]:
    """Products under their niche's target margin, per niche, with the price that reaches it."""
    out: dict[str, list[dict]] = {}
    for p in await db.fetchall(
            "SELECT sku, name, niche, material, color, price_agorot, est_grams, est_minutes, post_minutes "
            "FROM catalog.products WHERE active"):
        n = ctx["niches"].get(p["niche"]) or {}
        i = pricing._inputs(ctx, grams=float(p["est_grams"]), minutes=float(p["est_minutes"]), material=p["material"],
                            color=p["color"], labour_minutes=float(p["post_minutes"] or 0),
                            parts=ctx["bom"].get(p["sku"], 0), channel=None)
        e = unit_economics(i, ctx["settings"], gross_price=int(p["price_agorot"]), target_margin_pct=n.get("target_margin_pct"))
        if e.below_target:
            out.setdefault(p["niche"], []).append({"sku": p["sku"], "name": p["name"], **asdict(e)})
    return out


@router.get("/api/v1/analytics/scorecard", dependencies=MONEY)
async def scorecard(request: Request, days: int = Query(30, ge=7, le=365), format: str | None = Query(None, pattern=export.FORMAT)):
    db, end = request.app.state.db, business_today()
    cur, prev = end - timedelta(days=days), end - timedelta(days=2 * days)
    rows = {r["niche"]: r for r in await db.fetchall(SCORE_SQL, {"cur": cur, "prev": prev, "end": end})}
    niches = await db.fetchall(
        "SELECT slug, name, color, target_margin_pct::float AS target_margin_pct, archived, sort FROM catalog.niches "
        "ORDER BY sort, name")
    sig_day = await db.fetchval("SELECT max(day) FROM trend.signals")
    signals = await db.fetchall(
        "SELECT scope, subject, niche, direction, strength, change_pct, evidence FROM trend.signals WHERE day = %s "
        "ORDER BY strength DESC", (sig_day,)) if sig_day else []
    insights = {r["niche"]: r for r in await db.fetchall(
        "SELECT DISTINCT ON (niche) niche, text, day FROM trend.insights ORDER BY niche, day DESC")}
    shelf = {r["sku"]: int(r["free"]) for r in await db.fetchall(
        "SELECT sku, on_hand - reserved AS free FROM coordinator.finished_goods")}
    below = await _below_target(db, await pricing.context(db))
    out = []
    for n in niches:
        r = rows.get(n["slug"])
        if n["archived"] and not r:
            continue
        r = r or {}
        revenue, cost = int(r.get("revenue") or 0), int(r.get("cost") or 0)
        profit, hours = revenue - cost, float(r.get("hours") or 0)
        checked, failed = int(r.get("qc_checked") or 0), int(r.get("qc_failed") or 0)
        units, units_prev = int(r.get("units") or 0), int(r.get("units_prev") or 0)
        mine = [s for s in signals if s["niche"] == n["slug"]]
        niche_signal = next((s for s in mine if s["scope"] == "niche"), None)
        rising = [s for s in mine if s["scope"] == "sku" and s["direction"] == "rising"][:5]
        falling = [s for s in mine if s["scope"] == "sku" and s["direction"] == "falling"][:5]
        qc_pct = round(failed * 100 / checked, 1) if checked else None
        tips: list[dict] = []
        for s in rising[:3]:
            need = max(0, _units_7d(s) - shelf.get(s["subject"], 0))
            if need:
                tips.append({"kind": "batch", "sku": s["subject"], "quantity": min(need, 50),
                             "text": f"{s['subject']} is rising: queue {min(need, 50)} for stock",
                             "to": f"/catalog?niche={n['slug']}"})
        for b in below.get(n["slug"], [])[:3]:
            if b["recommended_price"]:
                tips.append({"kind": "price", "sku": b["sku"], "price_agorot": b["recommended_price"],
                             "text": f"{b['sku']} earns {b['margin_pct']}% (target {b['target_margin_pct']}%): "
                                     f"raise it to ₪{b['recommended_price'] / 100:.0f}", "to": "/finance?tab=products"})
        if qc_pct is not None and qc_pct >= QC_ALERT_PCT and checked >= 5:
            tips.append({"kind": "quality", "text": f"{qc_pct}% of checked pieces failed QC: review the checklist and "
                                                    "the print settings", "to": "/niches"})
        if niche_signal and niche_signal["direction"] == "falling":
            tips.append({"kind": "demand", "text": "Demand is falling: post about it on social, or offer a bundle",
                         "to": f"/social?niche={n['slug']}"})
        out.append({
            "niche": n["slug"], "name": n["name"], "color": n["color"], "target_margin_pct": n["target_margin_pct"],
            "revenue_agorot": revenue, "cost_agorot": cost, "profit_agorot": profit,
            "margin_pct": round(profit * 100 / revenue, 1) if revenue else None,
            "revenue_prev_agorot": int(r.get("revenue_prev") or 0),
            "profit_prev_agorot": int(r.get("revenue_prev") or 0) - int(r.get("cost_prev") or 0),
            "units": units, "units_prev": units_prev, "demand_pct": trend_pct(units, units_prev),
            "print_hours": round(hours, 1), "profit_per_print_hour_agorot": round(profit / hours) if hours > 0 else None,
            "qc_checked": checked, "qc_failed": failed, "qc_fail_pct": qc_pct,
            "leads": int(r.get("leads") or 0), "leads_prev": int(r.get("leads_prev") or 0),
            "direction": niche_signal["direction"] if niche_signal else None,
            "rising": [s["subject"] for s in rising], "falling": [s["subject"] for s in falling],
            "rising_detail": rising, "below_target": len(below.get(n["slug"], [])),
            "insight": (insights.get(n["slug"]) or {}).get("text"), "suggestions": tips,
        })
    total_rev = sum(x["revenue_agorot"] for x in out)
    if format:
        return export.table(out, SCORE_COLUMNS, name=f"printfarm-scorecard-{days}d-{export.stamp(end)}", fmt=format,
                            sheet="Scorecard")
    return {"days": days, "end": end.isoformat(), "niches": out, "revenue_agorot": total_rev,
            "signals_day": sig_day.isoformat() if sig_day else None,
            "rolled_at": await db.fetchval("SELECT max(rolled_at) FROM reporting.rollups")}


def _units_7d(signal: dict) -> int:
    """The units of the last 7 days, from a signal's first piece of evidence ("12 sold in the last 7 days …")."""
    for e in signal.get("evidence") or []:
        head = str(e).split(" sold in the last 7 days")[0]
        if head.isdigit():
            return int(head)
    return 0


@router.get("/api/v1/trend/signals", dependencies=[Depends(auth.require_auth)])   # demand, no money
async def signals(request: Request, niche: str | None = None):
    db, scope = request.app.state.db, niche_scope(niche)
    day = await db.fetchval("SELECT max(day) FROM trend.signals")
    rows = await db.fetchall(
        "SELECT s.signal_id, s.scope, s.subject, s.niche, s.direction, s.strength, s.change_pct, s.evidence, "
        "coalesce(p.name, n.name, s.subject) AS label FROM trend.signals s "
        "LEFT JOIN catalog.products p ON s.scope = 'sku' AND p.sku = s.subject "
        "LEFT JOIN catalog.niches n ON s.scope = 'niche' AND n.slug = s.subject "
        "WHERE s.day = %s" + (" AND s.niche = %s" if scope else "") + " ORDER BY s.strength DESC LIMIT 50",
        (day, *((scope,) if scope else ()))) if day else []
    insights = await db.fetchall(
        "SELECT DISTINCT ON (niche) niche, text, day FROM trend.insights" + (" WHERE niche = %s" if scope else "")
        + " ORDER BY niche, day DESC", (scope,) if scope else ())
    return {"day": day.isoformat() if day else None, "signals": rows, "insights": insights}
