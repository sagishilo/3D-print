"""Order Pipeline read side: board cards, one order's details, pipeline statistics and the build plates in use.
Orders change only through commands (orders.create / advance / qc / ship / cancel)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from printfarm.contracts.orders import PRODUCTS

from . import auth

router = APIRouter(prefix="/api/v1/orders", dependencies=[Depends(auth.require_auth)])
STUCK_AFTER = timedelta(hours=2)
WAITING = {"LEAD", "RECEIVED", "PERSONALIZING", "SCHEDULING", "QC", "NEEDS_ATTENTION"}   # states a person may be blocking

ORDER_SQL = """
  SELECT o.order_id, o.customer_name, o.customer_ref, o.channel, o.due_by, o.notes, o.subtotal_agorot, o.discount_agorot,
         o.shipping_agorot, o.total_agorot, o.units, o.created_by, o.created_at, s.state, s.context, s.updated_at,
         (SELECT max(h.at) FROM coordinator.saga_history h WHERE h.saga_id = o.order_id) AS stage_since,
         (SELECT coalesce(sum(CASE p.kind WHEN 'payment' THEN p.amount_agorot ELSE -p.amount_agorot END), 0)
            FROM coordinator.payments p WHERE p.order_id = o.order_id) AS paid_agorot
  FROM coordinator.orders o JOIN coordinator.sagas s ON s.saga_id = o.order_id
"""


def _ord_id(order_id: str) -> str:
    if not (0 < len(order_id) <= 40 and all(c.isalnum() or c in "-_" for c in order_id)):
        raise HTTPException(400, "bad order id")
    return order_id


def _status(state: str, ctx: dict) -> str:
    """One line for the card: what this order waits for."""
    items, units = ctx.get("items", {}), ctx.get("units", {})
    ready = sum(1 for i in items.values() if i.get("model_status") == "ready")
    printed = sum(1 for u in units.values() if u.get("status") == "printed")
    match state:
        case "LEAD":
            return "lead · confirm to start"
        case "RECEIVED":
            return "received · drag to Personalization"
        case "PERSONALIZING":
            failed = [k for k, i in items.items() if i.get("model_status") == "failed"]
            if failed:
                return f"model failed: item {', '.join(failed)}"
            return f"models ready {ready}/{len(items)}" + (" · drag to scheduling" if ready == len(items) else " · generating")
        case "SCHEDULING":
            return f"{sum(1 for u in units.values() if u.get('status') == 'queued')} unit(s) waiting for a plate"
        case "PRINTING":
            return f"printed {printed}/{len(units)} units"
        case "QC":
            return "passed · ready to ship" if ctx.get("qc", {}).get("status") == "passed" else "awaiting quality check"
        case "NEEDS_ATTENTION":
            return ctx.get("attention", {}).get("reason", "needs attention")
        case "SHIPPED":
            sh = ctx.get("shipped", {})
            return " · ".join(x for x in ("shipped", sh.get("carrier"), sh.get("tracking")) if x)
        case "CANCELLED":
            return f"cancelled: {ctx.get('cancelled', {}).get('reason') or 'no reason given'}"
    return state.lower()


def _card(r: dict, items: list[dict], plates: list[dict], now: datetime) -> dict:
    ctx = r["context"] or {}
    state = r["state"]
    stage = ctx.get("attention", {}).get("from", "RECEIVED") if state == "NEEDS_ATTENTION" else state
    since = r["stage_since"] or r["created_at"]
    model = {k: {"status": v.get("model_status"), "error": v.get("model_error")} for k, v in ctx.get("items", {}).items()}
    units = ctx.get("units", {})
    return {
        "order_id": r["order_id"], "customer_name": r["customer_name"], "customer_ref": r["customer_ref"],
        "channel": r["channel"], "due_by": r["due_by"], "created_at": r["created_at"], "state": state, "stage": stage,
        "stage_since": since, "stuck": state in WAITING and now - since > STUCK_AFTER,
        "status": _status(state, ctx), "total_agorot": r["total_agorot"], "paid_agorot": r["paid_agorot"],
        "units": r["units"], "units_printed": sum(1 for u in units.values() if u.get("status") == "printed"),
        "qc": ctx.get("qc", {}).get("status"),
        "items": [{"item_id": i["item_id"], "product": i["product"], "label": PRODUCTS.get(i["product"]).label
                   if i["product"] in PRODUCTS else i["product"], "text": i["text"], "material": i["material"],
                   "color": i["color"], "quantity": i["quantity"], "model": model.get(i["item_id"], {})} for i in items],
        "plates": plates,
    }


async def _cards(db, where: str, params: tuple, limit: int) -> list[dict]:
    rows = await db.fetchall(f"{ORDER_SQL} WHERE {where} ORDER BY o.created_at DESC LIMIT {int(limit)}", params)
    ids = [r["order_id"] for r in rows]
    items, plates = {}, {}
    if ids:
        for i in await db.fetchall("SELECT * FROM coordinator.order_items WHERE order_id = ANY(%s) ORDER BY order_id, "
                                   "item_id::int", (ids,)):
            items.setdefault(i["order_id"], []).append(i)
        for p in await db.fetchall(
                "SELECT DISTINCT j.order_id, p.plate_id, p.printer_id, p.status FROM fleet.jobs j "
                "JOIN fleet.plates p ON p.plate_id = j.plate_id WHERE j.order_id = ANY(%s) "
                "AND p.status IN ('scheduled', 'printing')", (ids,)):
            plates.setdefault(p["order_id"], []).append({k: p[k] for k in ("plate_id", "printer_id", "status")})
    now = datetime.now(UTC)
    return [_card(r, items.get(r["order_id"], []), plates.get(r["order_id"], []), now) for r in rows]


@router.get("")
async def orders(request: Request, closed_days: int = Query(7, ge=0, le=90), channel: str | None = None):
    """Open orders plus those shipped or cancelled in the last ``closed_days`` (the board's last column)."""
    where = "(s.state NOT IN ('SHIPPED', 'CANCELLED') OR s.updated_at > now() - make_interval(days => %s))"
    params: tuple = (closed_days,)
    if channel:
        where += " AND o.channel = %s"
        params += (channel,)
    return {"orders": await _cards(request.app.state.db, where, params, 500)}


@router.get("/stats")
async def stats(request: Request):
    """KPIs that need history: QC failures (7 days) and lead-to-ship time (30 days vs the 30 before)."""
    db = request.app.state.db
    qc = await db.fetchrow(
        "SELECT coalesce(sum(units_failed), 0) AS failed, coalesce(sum(units_checked), 0) AS checked "
        "FROM coordinator.qc_checks WHERE checked_at > now() - interval '7 days'") or {}
    lead = await db.fetchrow(
        "SELECT avg(extract(epoch FROM h.at - o.created_at)) FILTER (WHERE h.at > now() - interval '30 days') AS cur, "
        "avg(extract(epoch FROM h.at - o.created_at)) FILTER (WHERE h.at <= now() - interval '30 days') AS prev "
        "FROM coordinator.saga_history h JOIN coordinator.orders o ON o.order_id = h.saga_id "
        "WHERE h.to_state = 'SHIPPED' AND h.at > now() - interval '60 days'") or {}
    days = lambda v: round(float(v) / 86400, 1) if v is not None else None  # noqa: E731
    return {"qc_failed_units_7d": int(qc.get("failed") or 0), "qc_checked_units_7d": int(qc.get("checked") or 0),
            "lead_to_ship_days": days(lead.get("cur")), "lead_to_ship_days_prev": days(lead.get("prev"))}


@router.get("/plates")
async def plates(request: Request):
    """Build plates the Fleet Manager assigned that are not finished yet, with what is on them."""
    rows = await request.app.state.db.fetchall(
        "SELECT p.plate_id, p.printer_id, p.material, p.color, p.status, p.est_minutes, p.created_at, p.started_at, "
        "coalesce(json_agg(json_build_object('job_id', j.job_id, 'order_id', j.order_id)) FILTER (WHERE j.job_id IS NOT NULL), "
        "'[]') AS jobs FROM fleet.plates p LEFT JOIN fleet.jobs j ON j.plate_id = p.plate_id "
        "WHERE p.status IN ('scheduled', 'printing') GROUP BY p.plate_id ORDER BY p.created_at")
    return {"plates": rows}


@router.get("/{order_id}")
async def order_detail(order_id: str, request: Request):
    db = request.app.state.db
    cards = await _cards(db, "o.order_id = %s", (_ord_id(order_id),), 1)
    if not cards:
        raise HTTPException(404, f"no order {order_id}")
    row = await db.fetchrow(f"{ORDER_SQL} WHERE o.order_id = %s", (order_id,))
    ctx = row["context"] or {}
    items = await db.fetchall("SELECT * FROM coordinator.order_items WHERE order_id = %s ORDER BY item_id::int", (order_id,))
    return {
        **cards[0],
        "notes": row["notes"], "created_by": row["created_by"],
        "pricing": {"subtotal_agorot": row["subtotal_agorot"], "discount_agorot": row["discount_agorot"],
                    "shipping_agorot": row["shipping_agorot"], "total_agorot": row["total_agorot"],
                    "lines": [{"item_id": i["item_id"], "unit_price_agorot": i["unit_price_agorot"],
                               "line_total_agorot": i["line_total_agorot"]} for i in items]},
        "unit_status": [{"unit": k, **v, "item_text": ctx.get("items", {}).get(k.split(".")[0], {}).get("text", "")}
                        for k, v in ctx.get("units", {}).items()],
        "attention": ctx.get("attention"), "shipped": ctx.get("shipped"), "cancelled": ctx.get("cancelled"),
        "history": await db.fetchall(
            "SELECT from_state, to_state, message_type, at FROM coordinator.saga_history WHERE saga_id = %s "
            "ORDER BY at, id", (order_id,)),
        "payments": await db.fetchall(
            "SELECT payment_id, kind, amount_agorot, method, at, receipt_no, recorded_by FROM coordinator.payments "
            "WHERE order_id = %s ORDER BY at", (order_id,)),
        "qc_checks": await db.fetchall(
            "SELECT passed, units_failed, units_checked, reason, checked_by, checked_at FROM coordinator.qc_checks "
            "WHERE order_id = %s ORDER BY checked_at", (order_id,)),
        "jobs": await db.fetchall(
            "SELECT j.job_id, j.status, j.plate_id, p.printer_id, p.status AS plate_status FROM fleet.jobs j "
            "LEFT JOIN fleet.plates p ON p.plate_id = j.plate_id WHERE j.order_id = %s ORDER BY j.created_at", (order_id,)),
    }
