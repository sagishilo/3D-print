"""Instant Catalog read side, the 3MF upload and the batch estimate. Products change only through commands
(catalog.upsert / catalog.slice); a batch is queued with catalog.batch (a stock order, dispatched at once)."""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from printfarm.agents.personalization.catalog import BatchItem, InvalidThreeMf, estimate_batch, inspect_3mf
from printfarm.contracts.catalog import (
    CATEGORIES,
    DEMAND_DAYS,
    MAX_UPLOAD_BYTES,
    STOCK_CHANNEL,
    UPLOAD_BUCKET,
    trend_pct,
    valid_sku,
)

from . import auth
from .niches import niche_scope

router = APIRouter(prefix="/api/v1/catalog")
DEFAULT_BED = (256.0, 256.0)      # no printer known yet: a Bambu-size bed
SORTS = {"demand": "sold_7d DESC, p.name", "name": "p.name", "newest": "p.created_at DESC", "price": "p.price_agorot DESC"}
_ITEM = re.compile(r"^([A-Z0-9][A-Z0-9-]{1,31}):(\d{1,3})$")

PRODUCTS_SQL = f"""
  SELECT p.sku, p.name, p.category, p.material, p.color, p.price_agorot, p.file_name, p.sliced, p.printer_model,
         p.footprint_w, p.footprint_d, p.est_grams, p.est_minutes, p.estimated, p.active, p.created_at, p.updated_at,
         p.updated_by, p.niche,
         coalesce((SELECT json_agg(t.niche ORDER BY t.niche) FROM catalog.product_tags t WHERE t.sku = p.sku), '[]')
           AS tags,
         coalesce((SELECT f.on_hand - f.reserved FROM coordinator.finished_goods f WHERE f.sku = p.sku), 0) AS on_shelf,
         coalesce(d.sold_7d, 0) AS sold_7d, coalesce(d.sold_prev_7d, 0) AS sold_prev_7d,
         coalesce(d.in_production, 0) AS in_production
  FROM catalog.products p
  LEFT JOIN (
    SELECT i.sku,
           sum(i.quantity) FILTER (WHERE o.channel <> '{STOCK_CHANNEL}'
                                     AND o.created_at >= now() - interval '{DEMAND_DAYS} days') AS sold_7d,
           sum(i.quantity) FILTER (WHERE o.channel <> '{STOCK_CHANNEL}'
                                     AND o.created_at <  now() - interval '{DEMAND_DAYS} days'
                                     AND o.created_at >= now() - interval '{2 * DEMAND_DAYS} days') AS sold_prev_7d,
           sum(i.quantity) FILTER (WHERE o.channel = '{STOCK_CHANNEL}' AND s.state NOT IN ('SHIPPED', 'CANCELLED'))
             AS in_production
    FROM coordinator.order_items i
    JOIN coordinator.orders o ON o.order_id = i.order_id
    JOIN coordinator.sagas s ON s.saga_id = i.order_id
    WHERE i.sku <> '' AND s.state <> 'CANCELLED'
    GROUP BY i.sku
  ) d ON d.sku = p.sku
"""


IN_NICHE = "(p.niche = %s OR EXISTS (SELECT 1 FROM catalog.product_tags t WHERE t.sku = p.sku AND t.niche = %s))"


def _product(row: dict) -> dict:
    return {**row, "footprint_mm": [row.pop("footprint_w"), row.pop("footprint_d")],
            "trend_pct": trend_pct(row["sold_7d"], row["sold_prev_7d"])}


@router.get("/products", dependencies=[Depends(auth.require_auth)])
async def products(request: Request, category: str | None = None, sort: str = "demand", include_inactive: bool = False,
                   niche: str | None = None):
    if category is not None and category not in CATEGORIES:
        raise HTTPException(400, f"unknown category {category!r}")
    if sort not in SORTS:
        raise HTTPException(400, f"sort is one of {', '.join(SORTS)}")
    scope = niche_scope(niche)
    where, params = [], []
    if not include_inactive:
        where.append("p.active")
    if scope:
        # The niche's own products and the ones tagged with it.
        where.append(IN_NICHE)
        params += [scope, scope]
    base = list(where)
    if category:
        where.append("p.category = %s")
        params.append(category)
    sql = PRODUCTS_SQL + (f" WHERE {' AND '.join(where)}" if where else "") + f" ORDER BY {SORTS[sort]}"
    rows = [_product(r) for r in await request.app.state.db.fetchall(sql, tuple(params))]
    counts = await request.app.state.db.fetchall(
        "SELECT category, count(*) AS n FROM catalog.products AS p" + (f" WHERE {' AND '.join(base)}" if base else "")
        + " GROUP BY category", (scope, scope) if scope else ())
    return {"products": rows, "categories": list(CATEGORIES), "counts": {r["category"]: r["n"] for r in counts},
            "demand_days": DEMAND_DAYS}


@router.get("/products/{sku}", dependencies=[Depends(auth.require_auth)])
async def product(sku: str, request: Request):
    if not valid_sku(sku):
        raise HTTPException(400, "bad SKU")
    row = await request.app.state.db.fetchrow(PRODUCTS_SQL + " WHERE p.sku = %s", (sku,))
    if row is None:
        raise HTTPException(404, f"no SKU {sku}")
    return _product(row)


@router.post("/uploads", status_code=201)
async def upload(request: Request, name: str = Query("", max_length=120), _c: dict = Depends(auth.OPERATOR_CHANGE)):
    """The raw 3MF as the request body. Kept 7 days in the uploads object store under its SHA-256, for the
    personalization agent to take with catalog.upsert; the reply says what the file holds, to prefill the form."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"a template may be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"a template may be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(400, "send the 3MF file as the request body")
    try:
        info = await asyncio.to_thread(inspect_3mf, bytes(data))
    except InvalidThreeMf as exc:
        raise HTTPException(422, str(exc)) from None
    sha = hashlib.sha256(data).hexdigest()
    await request.app.state.bus.blob_put(UPLOAD_BUCKET, sha, bytes(data))
    return {"upload": sha, "name": name, "bytes": len(data), "info": info.as_dict()}


async def _available(db) -> dict[tuple[str, str], float]:
    """Grams free for new work per filament: on spools, minus scheduled plates' reservations and queued jobs."""
    out: dict[tuple[str, str], float] = {}
    for sql, sign in (
        ("SELECT material, color, sum(remaining_grams) AS g FROM inventory.spools WHERE status <> 'empty' GROUP BY 1, 2", 1),
        ("SELECT material, color, sum(grams) AS g FROM inventory.reservations GROUP BY 1, 2", -1),
        ("SELECT material, color, sum(est_grams) AS g FROM fleet.jobs WHERE status = 'queued' GROUP BY 1, 2", -1),
    ):
        for r in await db.fetchall(sql):
            key = (r["material"], r["color"])
            out[key] = out.get(key, 0.0) + sign * float(r["g"] or 0)
    return {k: max(0.0, v) for k, v in out.items()}


async def _bed(db) -> tuple[float, float]:
    row = await db.fetchrow("SELECT bed_w, bed_d FROM fleet.printers ORDER BY bed_w * bed_d DESC LIMIT 1")
    return (float(row["bed_w"]), float(row["bed_d"])) if row else DEFAULT_BED


@router.get("/estimate", dependencies=[Depends(auth.require_auth)])
async def estimate(request: Request, items: Annotated[list[str] | None, Query()] = None):
    """What a batch takes: ``items=SKU:quantity`` (repeated). Plates are packed as the scheduler packs them."""
    items = items or []
    if len(items) > 50:
        raise HTTPException(400, "at most 50 items per batch")
    wanted: dict[str, int] = {}
    for raw in items:
        m = _ITEM.match(raw)
        if not m:
            raise HTTPException(400, f"bad item {raw!r}: use SKU:quantity")
        wanted[m.group(1)] = wanted.get(m.group(1), 0) + int(m.group(2))
    db = request.app.state.db
    rows = await db.fetchall("SELECT * FROM catalog.products WHERE sku = ANY(%s)", (list(wanted),)) if wanted else []
    missing = sorted(set(wanted) - {r["sku"] for r in rows})
    if missing:
        raise HTTPException(404, f"not in the catalog: {', '.join(missing)}")
    batch = [BatchItem(r["sku"], wanted[r["sku"]], r["material"], r["color"], (r["footprint_w"], r["footprint_d"]),
                       r["est_grams"], r["est_minutes"], r["sliced"]) for r in rows]
    bed = await _bed(db)
    est = estimate_batch(batch, await _available(db), bed=bed)
    return {
        "skus": est.skus, "pieces": est.pieces, "plates": est.plates, "minutes": est.minutes, "grams": est.grams,
        "bed_mm": list(bed), "too_big": list(est.too_big),
        "materials": [{"material": m.material, "color": m.color, "need_grams": m.need_grams,
                       "available_grams": m.available_grams, "short_grams": m.short_grams} for m in est.materials],
        "unsliced": sorted(r["sku"] for r in rows if not r["sliced"]),
    }


async def resolve_lines(db, body: dict) -> dict:
    """Catalog lines of a new order or batch take the catalog's filament (and price, unless one was typed in)."""
    items = body.get("items")
    if not isinstance(items, list):
        return body
    skus = [i.get("sku") for i in items if isinstance(i, dict) and i.get("product") == "catalog"]
    if not all(isinstance(s, str) and valid_sku(s) for s in skus):
        raise HTTPException(400, "a catalog item needs a valid SKU")
    rows = {r["sku"]: r for r in await db.fetchall(
        "SELECT sku, name, material, color, price_agorot, active, niche FROM catalog.products WHERE sku = ANY(%s)",
        (skus,))} if skus else {}
    out = []
    for item in items:
        if isinstance(item, dict):
            # The niche is never the browser's to say: a catalog line is sold under its product's niche (a
            # snapshot), anything else under the coordinator's default (a nameplate is a personalised gift).
            item = {k: v for k, v in item.items() if k != "niche"}
            if item.get("product") == "catalog":
                p = rows.get(item["sku"])
                if p is None or not p["active"]:
                    raise HTTPException(400, f"SKU {item['sku']} is not in the catalog" if p is None
                                        else f"SKU {item['sku']} is switched off in the catalog")
                item = {**item, "material": p["material"], "color": p["color"], "text": item.get("text") or p["name"][:40],
                        "niche": p["niche"]}
                if item.get("unit_price_agorot") is None:
                    item["unit_price_agorot"] = p["price_agorot"]
        out.append(item)
    return {**body, "items": out}
