"""Filament read side: spools (with the grams reserved by scheduled plates), the reorder list (the inventory agent's
rule applied to every material), alerts, purchase orders and daily use. Changes go through commands."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request

from printfarm.agents.inventory.model import Policy, assess
from printfarm.contracts.stock import (
    DRY_AFTER_DAYS,
    HUMID_LEVEL,
    days_open,
    dry_after_days,
    item_reorder_qty,
    needs_drying,
    restock_suggestion,
)

from . import auth
from .jsread import decode
from .niches import niche_scope

router = APIRouter(prefix="/api/v1/inventory", dependencies=[Depends(auth.require_auth)])
OPEN_PO = ("pending_approval", "placed", "ordered")


@router.get("/spools")
async def spools(request: Request, include_empty: bool = False):
    db = request.app.state.db
    where = "" if include_empty else " WHERE status <> 'empty' OR printer_id IS NOT NULL"   # an empty spool still on a printer shows
    rows = await db.fetchall(
        "SELECT spool_id, material, color, brand, initial_grams, remaining_grams, printer_id, slot, status, location, tag, "
        f"weighed_at, created_at, opened_at, dried_at FROM inventory.spools{where} "
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
        # An item PO (part, packaging) shows the item's name where a filament PO shows its material.
        "SELECT p.po_id, coalesce(i.name, p.material) AS material, p.color, p.spools, p.est_cost_agorot, p.status, "
        "p.approval_id, p.supplier, p.total_agorot, p.vat_invoice, p.item_id, p.quantity::float AS quantity, "
        "p.ordered_at, p.received_at, p.created_at FROM inventory.purchase_orders p "
        "LEFT JOIN inventory.items i ON i.item_id = p.item_id "
        "WHERE p.status = ANY(%s) OR p.created_at > now() - make_interval(days => %s) ORDER BY p.created_at DESC LIMIT 200",
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


# ---------------------------------------------------------------- Wave 1 / M4: finished goods, items, condition

FG_SQL = """
  SELECT p.sku, p.name, p.niche, p.active, coalesce(f.on_hand, 0) AS on_hand, coalesce(f.reserved, 0) AS reserved,
         coalesce(f.min_stock, 0) AS min_stock, coalesce(f.location, '') AS location, f.updated_at,
         coalesce((SELECT sum(i.quantity) FROM coordinator.order_items i
                     JOIN coordinator.orders o ON o.order_id = i.order_id
                     JOIN coordinator.sagas s ON s.saga_id = i.order_id
                    WHERE i.sku = p.sku AND o.channel = 'stock' AND s.state NOT IN ('SHIPPED', 'CANCELLED')), 0)
           AS in_production,
         coalesce((SELECT sum(delta_on_hand) FROM coordinator.fg_ledger WHERE sku = p.sku AND reason = 'built'), 0)
           AS built_units,
         coalesce((SELECT sum(net_agorot) FROM reporting.sku_costs_v c WHERE c.product_type = p.sku AND c.stock), 0)
           AS built_cost_agorot
  FROM catalog.products p LEFT JOIN coordinator.finished_goods f ON f.sku = p.sku
"""


@router.get("/finished-goods")
async def finished_goods(request: Request, niche: str | None = None, all_skus: bool = False):
    """Pieces on the shelf per SKU: on hand, reserved for open orders, free, the minimum, what is being made, the
    average cost of a piece (stock-batch filament and power per piece built) and what a restock would queue."""
    scope = niche_scope(niche)
    where = ["(p.active OR f.on_hand > 0)"]
    params: list = []
    if not all_skus:
        where.append("(f.sku IS NOT NULL)")
    if scope:
        where.append("p.niche = %s")
        params.append(scope)
    rows = await request.app.state.db.fetchall(
        FG_SQL + " WHERE " + " AND ".join(where) + " ORDER BY p.name", tuple(params))
    out = []
    for r in rows:
        unit_cost = round(int(r["built_cost_agorot"]) / int(r["built_units"])) if int(r["built_units"]) > 0 else None
        free = int(r["on_hand"]) - int(r["reserved"])
        out.append({**{k: r[k] for k in ("sku", "name", "niche", "active", "on_hand", "reserved", "min_stock", "location",
                                         "updated_at", "in_production")},
                    "free": free, "unit_cost_agorot": unit_cost,
                    "value_agorot": unit_cost * int(r["on_hand"]) if unit_cost is not None else None,
                    "low": int(r["min_stock"]) > 0 and free < int(r["min_stock"]),
                    "restock": restock_suggestion(int(r["on_hand"]), int(r["reserved"]), int(r["min_stock"]),
                                                  int(r["in_production"]))})
    return {"items": out, "value_agorot": sum(x["value_agorot"] or 0 for x in out),
            "low": sum(1 for x in out if x["low"])}


@router.get("/finished-goods/{sku}/ledger")
async def finished_goods_ledger(sku: str, request: Request, limit: int = Query(50, ge=1, le=500)):
    return {"ledger": await request.app.state.db.fetchall(
        "SELECT delta_on_hand, delta_reserved, reason, order_id, by, note, at FROM coordinator.fg_ledger WHERE sku = %s "
        "ORDER BY at DESC, id DESC LIMIT %s", (sku, limit))}


@router.get("/items")
async def items(request: Request, include_archived: bool = False):
    """Parts, packaging and consumables: stock, minimum, what is on order, which SKUs use them."""
    rows = await request.app.state.db.fetchall(
        """SELECT i.*, i.on_hand::float AS on_hand, i.min_qty::float AS min_qty, i.reorder_qty::float AS reorder_qty,
             (SELECT coalesce(sum(p.quantity), 0)::float FROM inventory.purchase_orders p
               WHERE p.item_id = i.item_id AND p.status = ANY(%s)) AS on_order,
             coalesce((SELECT json_agg(json_build_object('sku', b.sku, 'qty', b.qty::float) ORDER BY b.sku)
                         FROM inventory.bom b WHERE b.item_id = i.item_id), '[]') AS used_by,
             (SELECT coalesce(-sum(l.delta), 0)::float FROM inventory.item_ledger l WHERE l.item_id = i.item_id
               AND l.delta < 0 AND l.reason IN ('made', 'shipped') AND l.at > now() - interval '30 days') AS used_30d
           FROM inventory.items i WHERE NOT i.archived OR %s ORDER BY i.kind, i.name""",
        (list(OPEN_PO), include_archived))
    out = []
    for r in rows:
        suggest = item_reorder_qty(r["on_hand"], r["on_order"], r["min_qty"], r["reorder_qty"])
        out.append({**r, "low": r["min_qty"] > 0 and r["on_hand"] < r["min_qty"], "suggested_qty": suggest,
                    "suggested_cost_agorot": round(suggest * r["cost_agorot"])})
    return {"items": out}


@router.get("/bom/{sku}")
async def bom(sku: str, request: Request):
    return {"sku": sku, "lines": await request.app.state.db.fetchall(
        "SELECT b.item_id, b.qty::float AS qty, i.name, i.kind, i.unit, i.cost_agorot FROM inventory.bom b "
        "JOIN inventory.items i USING (item_id) WHERE b.sku = %s ORDER BY i.kind, i.name", (sku,))}


async def ams_humidity(bus) -> list[dict]:
    """AMS units reporting a humidity index at or above HUMID_LEVEL (from the printers' live status)."""
    out = []
    try:
        keys = await bus.kv_keys("printer_status")
    except Exception:  # noqa: BLE001 - no bucket yet: nothing to report
        return out
    for key in keys:
        raw = await bus.kv_get("printer_status", key)
        if not raw:
            continue
        env = decode(raw)
        status = env.get("data") if isinstance(env, dict) else None
        if not isinstance(status, dict):
            continue
        for unit in ((status.get("hardware") or {}).get("ams") or []):
            level = unit.get("humidity_level")
            if isinstance(level, int) and level >= HUMID_LEVEL:
                out.append({"printer_id": status.get("printer_id") or key, "unit": unit.get("unit"), "level": level})
    return out


@router.get("/condition")
async def condition(request: Request):
    """Filament condition: open spools due for drying (days open against the material's limit) and humid AMS units."""
    now = datetime.now(UTC)
    rows = await request.app.state.db.fetchall(
        "SELECT spool_id, material, color, brand, printer_id, slot, location, opened_at, dried_at, remaining_grams "
        "FROM inventory.spools WHERE status <> 'empty' AND opened_at IS NOT NULL ORDER BY material, color, spool_id")
    spools_out = []
    for r in rows:
        d = days_open(r["opened_at"], r["dried_at"], now)
        spools_out.append({**r, "days_open": round(d, 1) if d is not None else None,
                           "dry_after_days": dry_after_days(r["material"]),
                           "needs_drying": needs_drying(r["material"], r["opened_at"], r["dried_at"], now)})
    return {"spools": spools_out, "due": sum(1 for s in spools_out if s["needs_drying"]),
            "humid": await ams_humidity(request.app.state.bus), "humid_level": HUMID_LEVEL,
            "dry_after_days": DRY_AFTER_DAYS}
