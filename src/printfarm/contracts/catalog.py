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
