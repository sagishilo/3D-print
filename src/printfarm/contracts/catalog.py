"""Instant Catalog (Phase 8): ready-made products (SKUs) printed from a 3MF template, shared by the personalization
agent (owner of the catalog schema), the coordinator, the gateway and the dashboard.

A SKU is one printable piece. Its template is a 3MF file:

* **sliced** (exported from Bambu Studio / OrcaSlicer as a sliced plate, ``Metadata/plate_1.gcode`` inside): the file
  goes to the printer as is, so each piece prints on a plate of its own (the slicer arranged it);
* **unsliced** (a model only): packed with other parts on a plate; a simulated printer prints it, a real printer
  needs it sliced first (Slice now, or upload a sliced export).
"""

from __future__ import annotations

import re

SKU_PATTERN = r"^[A-Z0-9][A-Z0-9-]{1,31}$"
SKU_RE = re.compile(SKU_PATTERN)

CATEGORIES = ("stands", "displays", "storage", "home", "gifts", "kids", "seasonal", "other")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024          # one 3MF template
UPLOAD_BUCKET = "uploads"                     # NATS object store the gateway puts uploads into (7-day TTL)
STOCK_CHANNEL = "stock"                       # a batch printed for the shelf is an order on this channel

DEMAND_DAYS = 7

# Product images: the 3MF's own preview, or a PNG / JPEG / WebP the operator uploads (the uploads bucket, then
# catalog.upsert with ``image_upload``). Recognised by their first bytes, never by a name or a declared type.
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")


def image_type(data: bytes) -> str | None:
    """The type of a picture we accept, from its magic bytes; None for anything else (SVG, GIF, HTML, ...)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def delete_blocker(refs: dict) -> str | None:
    """Why a SKU cannot be deleted, from the counts of what refers to it (``REFERENCES_SQL``); None: delete it.
    Order lines, stock on the shelf and its history, and store listings keep pointing at a SKU: such a product is
    switched off instead, so order history, margins and reports stay intact."""
    why = []
    if refs.get("orders"):
        n = refs["orders"]
        why.append(f"{n} order{'' if n == 1 else 's'}")
    if refs.get("stock"):
        why.append("finished stock on the shelf or in its history")
    if refs.get("store_maps"):
        n = refs["store_maps"]
        why.append(f"{n} store listing{'' if n == 1 else 's'}")
    if not why:
        return None
    return f"it is used by {' and '.join(why)}: switch it off instead, so the history stays intact"


# What refers to a SKU outside the catalog (read-only; the gateway and the personalization agent both check).
REFERENCES_SQL = """
  SELECT (SELECT count(DISTINCT order_id) FROM coordinator.order_items WHERE sku = %(sku)s) AS orders,
         (SELECT count(*) FROM coordinator.finished_goods WHERE sku = %(sku)s AND (on_hand > 0 OR reserved > 0))
         + (SELECT count(*) FROM coordinator.fg_ledger WHERE sku = %(sku)s) AS stock,
         (SELECT count(*) FROM integrations.sku_map WHERE product = 'catalog' AND sku = %(sku)s) AS store_maps
"""

# Niches (Wave 1 / M3): each product has one main niche (catalog.products.niche) and optional extra tags. Order lines
# keep the niche they were sold under. "unassigned" always exists and cannot be archived.
NICHE_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"
NICHE_RE = re.compile(NICHE_PATTERN)
UNASSIGNED = "unassigned"
NAMEPLATE_NICHE = "gifts"                   # a personalised nameplate is sold as a personalised gift
NICHE_COLORS = 8                            # the --niche-1 .. --niche-8 colour tokens


def valid_sku(sku: str) -> bool:
    return bool(SKU_RE.match(sku))


def valid_niche(slug: str) -> bool:
    return bool(NICHE_RE.match(slug))


def trend_pct(now: int, before: int) -> float | None:
    """Change of units sold against the previous window; None when there is nothing to compare with."""
    if before <= 0:
        return None
    return round((now - before) * 100 / before, 1)
