"""Niches read side (Wave 1 / M3) and the shared ``?niche=`` filter.

A niche groups products (catalog.products.niche, plus extra tags in catalog.product_tags). Every list and report
endpoint takes ``?niche=<slug>``; ``niche_scope`` validates it once, so endpoints only add their own WHERE clause.
Niches change only through commands (catalog.niche.upsert / archive / assign), handled by the personalization agent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from printfarm.contracts.catalog import valid_niche

from . import auth

router = APIRouter(prefix="/api/v1/niches", dependencies=[Depends(auth.require_auth)])

NICHES_SQL = """
  SELECT n.slug, n.name, n.color, n.icon, n.sort, n.archived, n.default_material, n.default_colors,
         n.target_margin_pct::float AS target_margin_pct, n.qc_checklist, n.packaging, n.updated_by, n.updated_at,
         (SELECT count(*) FROM catalog.products p WHERE p.niche = n.slug AND p.active) AS products,
         (SELECT count(*) FROM catalog.product_tags t JOIN catalog.products p ON p.sku = t.sku
           WHERE t.niche = n.slug AND p.active) AS tagged
  FROM catalog.niches n
"""


def niche_scope(niche: str | None) -> str | None:
    """The niche a request is filtered to: None (all), or a valid slug (400 otherwise)."""
    if niche is None or niche in ("", "all"):
        return None
    if not valid_niche(niche):
        raise HTTPException(400, f"bad niche {niche!r}")
    return niche


@router.get("")
async def niches(request: Request, include_archived: bool = False):
    """Niches in their order (Unassigned last), with how many active products each holds."""
    rows = await request.app.state.db.fetchall(
        NICHES_SQL + " WHERE NOT n.archived OR %s ORDER BY n.archived, n.sort, n.name", (include_archived,))
    return {"niches": rows}


@router.get("/{slug}")
async def niche(slug: str, request: Request):
    row = await request.app.state.db.fetchrow(NICHES_SQL + " WHERE n.slug = %s", (niche_scope(slug) or "",))
    if row is None:
        raise HTTPException(404, f"no niche {slug}")
    return row
