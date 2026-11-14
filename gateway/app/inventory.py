"""Filament read side: spools (with the grams reserved by scheduled plates), the reorder list (the inventory agent's
rule applied to every material), alerts, purchase orders and daily use. Changes go through commands."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from printfarm.agents.inventory.model import Policy, assess

from . import auth

router = APIRouter(prefix="/api/v1/inventory", dependencies=[Depends(auth.require_auth)])
OPEN_PO = ("pending_approval", "placed", "ordered")


@router.get("/spools")
async def spools(request: Request, include_empty: bool = False):
    db = request.app.state.db
    where = "" if include_empty else " WHERE status <> 'empty' OR printer_id IS NOT NULL"   # an empty spool still on a printer shows
    rows = await db.fetchall(
        "SELECT spool_id, material, color, brand, initial_grams, remaining_grams, printer_id, slot, status, location, tag, "
        f"weighed_at, created_at FROM inventory.spools{where} "
        "ORDER BY status = 'loaded' DESC, printer_id NULLS LAST, material, color, remaining_grams")
    reservations = await db.fetchall(
        "SELECT plate_id, printer_id, material, color, grams, created_at FROM inventory.reservations ORDER BY created_at")
    return {"spools": rows, "reservations": reservations}


@router.get("/materials")
async def materials(request: Request):
    """The reorder list: every material and color with its stock position and what the rule says."""
    db = request.app.state.db
    rows = await db.fetchall(
        """SELECT m.*,
             (SELECT coalesce(sum(s.remaining_grams), 0) FROM inventory.spools s
               WHERE s.material = m.material AND s.color = m.color AND s.status <> 'empty') AS on_hand,
             (SELECT count(*) FROM inventory.spools s WHERE s.material = m.material AND s.color = m.color
               AND s.status = 'loaded') AS spools_loaded,
             (SELECT count(*) FROM inventory.spools s WHERE s.material = m.material AND s.color = m.color
               AND s.status = 'stock') AS spools_stock,
             (SELECT coalesce(sum(p.spools), 0) FROM inventory.purchase_orders p WHERE p.material = m.material
               AND p.color = m.color AND p.status = ANY(%s)) AS on_order_spools,
             (SELECT coalesce(sum(r.grams), 0) FROM inventory.reservations r WHERE r.material = m.material
               AND r.color = m.color) AS reserved,
             (SELECT coalesce(-sum(l.delta_grams), 0) FROM inventory.ledger l WHERE l.material = m.material
               AND l.color = m.color AND l.delta_grams < 0 AND l.reason IN ('print', 'failed_print')
               AND l.at > now() - interval '14 days') AS used_14d
           FROM inventory.materials m ORDER BY m.material, m.color""", (list(OPEN_PO),))
    out = []
    for m in rows:
        policy = Policy(m["grams_per_spool"], m["cost_per_spool_agorot"], m["lead_time_days"], m["safety_stock_days"],
                        m["review_period_days"], m["baseline_daily_grams"], m["auto_approve_limit_agorot"],
                        m["min_stock_grams"], m["reorder_mode"])
        a = assess(policy, m["on_hand"], m["on_order_spools"] * m["grams_per_spool"], m["used_14d"])
        out.append({
            "material": m["material"], "color": m["color"], "supplier": m["supplier"], "mode": m["reorder_mode"],
            "min_stock_grams": m["min_stock_grams"], "lead_time_days": m["lead_time_days"],
            "buffer_days": m["safety_stock_days"], "grams_per_spool": m["grams_per_spool"],
            "cost_per_spool_agorot": m["cost_per_spool_agorot"], "auto_approve_limit_agorot": m["auto_approve_limit_agorot"],
            "on_hand_grams": round(m["on_hand"], 1), "reserved_grams": round(m["reserved"], 1),
            "on_order_spools": m["on_order_spools"], "spools_loaded": m["spools_loaded"], "spools_stock": m["spools_stock"],
            "daily_grams": a.daily_grams, "days_left": a.days_left, "days_to_trigger": a.days_to_trigger,
            "state": a.state, "below_min": a.below_min, "suggested_spools": a.spools,
            "suggested_cost_agorot": a.est_cost_agorot, "needs_approval": a.needs_approval,
        })
    return {"materials": out}


@router.get("/alerts")
async def alerts(request: Request, closed_days: int = Query(0, ge=0, le=30)):
    """Open alerts (plus those closed in the last ``closed_days``), newest first."""
    return {"alerts": await request.app.state.db.fetchall(
        "SELECT alert_id, kind, severity, material, color, spool_id, printer_id, plate_id, needed_grams, available_grams, "
        "message, opened_at, resolved_at, resolved_by FROM inventory.alerts "
        "WHERE resolved_at IS NULL OR resolved_at > now() - make_interval(days => %s) ORDER BY opened_at DESC LIMIT 200",
        (closed_days,))}


@router.get("/pos")
async def purchase_orders(request: Request, days: int = Query(30, ge=1, le=365)):
    db = request.app.state.db
    pos = await db.fetchall(
        "SELECT po_id, material, color, spools, est_cost_agorot, status, approval_id, supplier, total_agorot, vat_invoice, "
        "ordered_at, received_at, created_at FROM inventory.purchase_orders "
        "WHERE status = ANY(%s) OR created_at > now() - make_interval(days => %s) ORDER BY created_at DESC LIMIT 200",
        (list(OPEN_PO), days))
    spend = await db.fetchrow(
        "SELECT coalesce(sum(total_agorot), 0) AS total, count(*) AS n FROM inventory.purchase_orders "
        "WHERE ordered_at > now() - interval '30 days'") or {}
    return {"pos": pos, "spend_30d_agorot": int(spend.get("total") or 0), "ordered_30d": int(spend.get("n") or 0)}


@router.get("/usage")
async def usage(request: Request, days: int = Query(14, ge=1, le=90)):
    """Filament printed per day (finished and failed plates), per material and color."""
    rows = await request.app.state.db.fetchall(
        "SELECT (at AT TIME ZONE 'UTC')::date AS day, material, color, round(-sum(delta_grams)::numeric, 1) AS grams "
        "FROM inventory.ledger WHERE delta_grams < 0 AND reason IN ('print', 'failed_print') "
        "AND at > now() - make_interval(days => %s) GROUP BY 1, 2, 3 ORDER BY 1", (days,))
    return {"days": days, "usage": [{**r, "day": str(r["day"]), "grams": float(r["grams"])} for r in rows]}
