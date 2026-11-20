"""Store orders read side (Wave 2 / W2-A): what the stores (Shopify) sent, what became of it, the "map this" queue
and what we sent back (tracking). Operators run the shop, so they see it; changes go through the commands
integrations.map_sku and integrations.store_order, handled by the integrations agent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from printfarm.agents.integrations.connectors import REGISTRY
from printfarm.agents.integrations.stores import StoreConnector

from . import auth

router = APIRouter(dependencies=[Depends(auth.OPERATOR)])
STATUSES = ("needs_mapping", "waiting_payment", "imported", "cancelled", "skipped")


def queue(rows: list[dict]) -> list[dict]:
    """The "map this" queue (pure): each unmapped store SKU once, with its title, the orders that wait for it and
    the line properties customers filled in (so a person can pick the one that holds the nameplate's text)."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        payload = r.get("payload") or {}
        for p in r.get("problems") or []:
            if p.get("kind") != "unmapped":
                continue
            q = out.setdefault((r["connector_id"], p["key"]), {
                "connector_id": r["connector_id"], "external_key": p["key"], "title": p.get("title", ""),
                "orders": [], "properties": [], "variants": []})
            if r["name"] not in q["orders"]:
                q["orders"].append(r["name"])
            for line in payload.get("lines", []):
                key = (line.get("sku") or "").strip() or f"title:{(line.get('title') or '').strip()[:110]}"
                if key != p["key"]:
                    continue
                for prop in line.get("properties") or {}:
                    if prop not in q["properties"]:
                        q["properties"].append(prop)
                if line.get("variant") and line["variant"] not in q["variants"]:
                    q["variants"].append(line["variant"])
    return sorted(out.values(), key=lambda q: (-len(q["orders"]), q["external_key"]))


@router.get("/api/v1/store/orders")
async def store_orders(request: Request, status: str | None = None, limit: int = Query(100, ge=1, le=500)):
    db = request.app.state.db
    where = "WHERE status = %s" if status in STATUSES else ""
    rows = await db.fetchall(
        "SELECT connector_id, external_id, name, customer, total_agorot, currency, financial_status, placed_at, status, "
        f"order_id, problems, seen_at, imported_at, updated_at FROM integrations.store_orders {where} "
        "ORDER BY coalesce(placed_at, seen_at) DESC LIMIT %s", (*((status,) if where else ()), limit))
    counts = await db.fetchall("SELECT status, count(*) AS n FROM integrations.store_orders GROUP BY 1")
    waiting = await db.fetchall(
        "SELECT connector_id, name, problems, payload FROM integrations.store_orders WHERE status = 'needs_mapping'")
    stores = await db.fetchall(
        "SELECT connector_id, label, enabled, mode FROM integrations.connectors WHERE kind = 'store' ORDER BY label")
    return {"orders": rows, "counts": {s: 0 for s in STATUSES} | {r["status"]: int(r["n"]) for r in counts},
            "queue": queue(waiting), "stores": stores}


@router.get("/api/v1/store/maps")
async def store_maps(request: Request, connector_id: str | None = None):
    rows = await request.app.state.db.fetchall(
        "SELECT connector_id, external_key, product, sku, material, color, text_property, updated_by, updated_at "
        "FROM integrations.sku_map" + (" WHERE connector_id = %s" if connector_id else "") +
        " ORDER BY connector_id, external_key", (connector_id,) if connector_id else ())
    return {"maps": rows, "stores": sorted(c.id for c in REGISTRY.values() if isinstance(c, StoreConnector))}


@router.get("/api/v1/store/pushes")
async def store_pushes(request: Request, limit: int = Query(50, ge=1, le=500)):
    rows = await request.app.state.db.fetchall(
        "SELECT id, connector_id, kind, external_id, order_id, payload, status, attempts, next_at, error, created_at, "
        "sent_at FROM integrations.pushes ORDER BY id DESC LIMIT %s", (limit,))
    return {"pushes": rows}


async def waiting_counts(db) -> dict:
    """For Today: store orders that wait for a person, and tracking a store would not take."""
    rows = await db.fetchall(
        "SELECT (SELECT count(*) FROM integrations.store_orders WHERE status = 'needs_mapping') AS waiting, "
        "(SELECT min(seen_at) FROM integrations.store_orders WHERE status = 'needs_mapping') AS since, "
        "(SELECT count(*) FROM integrations.pushes WHERE status = 'failed') AS failed_pushes")
    return dict(rows[0]) if rows else {}
