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


def valid_sku(sku: str) -> bool:
    return bool(SKU_RE.match(sku))


def trend_pct(now: int, before: int) -> float | None:
    """Change of units sold against the previous window; None when there is nothing to compare with."""
    if before <= 0:
        return None
    return round((now - before) * 100 / before, 1)
