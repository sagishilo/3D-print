"""Stock rules beyond filament grams (Wave 1 / M4), pure and shared by the agents, the gateway and the tests.

* Finished goods: how many shelf units a new order line takes, and what restocking a SKU suggests.
* Items (bill of materials): what a shipped line uses, and what to reorder.
* Filament condition: when an open spool should be dried, and when an AMS is too humid.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

# Days an opened spool stays good before it should be dried (hygroscopic materials are short). Unknown: 30.
DRY_AFTER_DAYS: dict[str, int] = {"PLA": 60, "PETG": 21, "ABS": 30, "ASA": 30, "TPU": 7, "PA": 3, "NYLON": 3,
                                  "PC": 7, "PVA": 2}
DEFAULT_DRY_AFTER_DAYS = 30
# Bambu AMS humidity index 1 (dry) .. 5 (wet): from this level the AMS needs fresh desiccant.
HUMID_LEVEL = 4

ITEM_KINDS = ("part", "packaging", "consumable")
MADE_KINDS = ("part", "consumable")      # used when a piece is made (printed and assembled)
SHIPPED_KINDS = ("packaging",)           # used when a piece leaves to a customer


def take_from_stock(lines: Iterable[tuple[str, int]], available: Mapping[str, int]) -> list[int]:
    """Shelf units each order line takes: (sku, quantity) in order, first come first served per SKU.
    A line without a SKU ("" = not a catalog line) takes none."""
    left = {k: max(0, v) for k, v in available.items()}
    out = []
    for sku, qty in lines:
        n = min(qty, left.get(sku, 0)) if sku else 0
        if n:
            left[sku] -= n
        out.append(n)
    return out


def restock_suggestion(on_hand: int, reserved: int, min_stock: int, in_production: int) -> int:
    """Pieces to queue so the shelf is back at its minimum once what is printing arrives (0: nothing to do)."""
    return max(0, min_stock - (on_hand - reserved) - in_production)


@dataclass(frozen=True)
class Use:
    item_id: str
    qty: float
    reason: str      # "made" | "shipped"


def bom_use(bom: Mapping[str, list[tuple[str, float, str]]], lines: Iterable[tuple[str, int, int]], *, to_shelf: bool) -> list[Use]:
    """Items used by a shipment. ``bom``: sku -> [(item_id, qty per piece, kind)]; ``lines``: (sku, quantity,
    from_stock). Parts and consumables are used for pieces made for it (not those taken from the shelf, which were
    used when the stock batch was made); packaging for every piece that leaves to a customer (none to the shelf)."""
    totals: dict[tuple[str, str], float] = {}
    for sku, quantity, from_stock in lines:
        made = max(0, quantity - from_stock)
        for item_id, per, kind in bom.get(sku, []):
            if kind in MADE_KINDS and made:
                totals[(item_id, "made")] = totals.get((item_id, "made"), 0.0) + per * made
            if kind in SHIPPED_KINDS and not to_shelf and quantity:
                totals[(item_id, "shipped")] = totals.get((item_id, "shipped"), 0.0) + per * quantity
    return [Use(i, round(q, 3), r) for (i, r), q in sorted(totals.items())]


def item_reorder_qty(on_hand: float, on_order: float, min_qty: float, reorder_qty: float) -> float:
    """What to order for an item below its minimum (counting what is on order): the reorder quantity, at least
    enough to reach the minimum; 0 when nothing is needed."""
    position = on_hand + on_order
    if min_qty <= 0 or position >= min_qty:
        return 0.0
    return max(reorder_qty, min_qty - position)


def dry_after_days(material: str) -> int:
    return DRY_AFTER_DAYS.get(material.upper(), DEFAULT_DRY_AFTER_DAYS)


def days_open(opened_at: datetime | None, dried_at: datetime | None, now: datetime) -> float | None:
    """Days since the spool was opened or last dried (None: never opened)."""
    start = max((t for t in (opened_at, dried_at) if t is not None), default=None)
    return None if start is None else max(0.0, (now - start).total_seconds() / 86400)


def needs_drying(material: str, opened_at: datetime | None, dried_at: datetime | None, now: datetime) -> bool:
    d = days_open(opened_at, dried_at, now)
    return d is not None and d >= dry_after_days(material)
