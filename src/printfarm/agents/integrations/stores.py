"""Store connectors (Wave 2 / W2-A): what every online store (Shopify now; WooCommerce or Etsy later) plugs into,
and the pure rules that turn a store's order into one of ours.

A store adapter only has to *read* its orders into ``StoreOrder`` (a normalised shape: lines with SKU, title,
quantity, price and the customer's line properties such as "Name") and *send back* a shipment's tracking. Everything
else is shared and pure, here:

* ``resolve``: each line becomes one of our order lines through the SKU map: a catalog SKU (a store SKU equal to an
  active catalog SKU needs no mapping), a nameplate in a filament with its text from a line property, or a line to
  ignore (gift wrap). A line nobody mapped goes to the "map this" queue; nothing is guessed.
* ``decide``: what to do with the order now: import it, wait for its payment, wait for a mapping, or leave it (cancelled
  at the store, skipped by a person, imported already). An imported order is never imported twice: its id is fixed
  by the store's id.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from printfarm.agents.integrations.framework import Connector, Record, SyncContext, SyncResult
from printfarm.contracts.orders import PRODUCTS

MAX_LINES = 20                   # an order of ours holds at most this many lines (CreateOrder)
MAX_QUANTITY = 50


def agorot(amount: str | float | int | None) -> int:
    """A store's money string ("49.90") as agorot, rounded half up. Missing or bad: 0."""
    if amount is None or amount == "":
        return 0
    try:
        return int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return 0


@dataclass(frozen=True)
class StoreLine:
    sku: str
    title: str
    quantity: int
    unit_price_agorot: int
    variant: str = ""
    properties: Mapping[str, str] = field(default_factory=dict)   # what the customer typed ("Name": "Noa")

    @property
    def key(self) -> str:
        """The SKU map's key: the SKU, or the product title when the store has no SKU for it."""
        return self.sku.strip() or f"title:{self.title.strip()[:110]}"


@dataclass(frozen=True)
class StoreOrder:
    external_id: str
    name: str                    # the number customers see, e.g. #1001
    customer_name: str
    customer_ref: str            # e-mail or phone
    placed_at: datetime | None
    paid: bool
    financial_status: str
    cancelled: bool
    lines: tuple[StoreLine, ...]
    total_agorot: int
    shipping_agorot: int = 0
    discount_agorot: int = 0
    currency: str = "ILS"
    note: str = ""
    updated_at: str = ""
    ship_to: Mapping[str, str] = field(default_factory=dict)   # name, street, city, zip, phone (W2-B ship form)

    def to_payload(self) -> dict:
        d = asdict(self)
        d["placed_at"] = self.placed_at.isoformat() if self.placed_at else None
        d["lines"] = [dict(asdict(line), properties=dict(line.properties)) for line in self.lines]
        d["ship_to"] = dict(self.ship_to)
        return d

    @classmethod
    def from_payload(cls, d: Mapping) -> StoreOrder:
        placed = d.get("placed_at")
        return cls(**{**{k: d[k] for k in d if k in cls.__dataclass_fields__},
                      "placed_at": datetime.fromisoformat(placed) if placed else None,
                      "lines": tuple(StoreLine(**{k: v for k, v in line.items() if k in StoreLine.__dataclass_fields__})
                                     for line in d.get("lines", []))})


@dataclass(frozen=True)
class SkuMap:
    product: str                 # catalog | nameplate | ignore
    sku: str = ""
    material: str = "PLA"
    color: str = "black"
    text_property: str = "Name"


@dataclass(frozen=True)
class Problem:
    kind: str                    # unmapped (the "map this" queue) | problem (a person must look)
    key: str
    title: str
    message: str


@dataclass(frozen=True)
class Resolution:
    lines: tuple[dict, ...]      # OrderLine fields
    problems: tuple[Problem, ...]
    ignored: tuple[str, ...] = ()  # titles of lines mapped as not printed (gift wrap)

    @property
    def unmapped(self) -> list[str]:
        return [p.key for p in self.problems if p.kind == "unmapped"]


def _prop(props: Mapping[str, str], name: str) -> str:
    want = name.strip().casefold()
    for k, v in props.items():
        if k.strip().casefold() == want:
            return str(v).strip()
    return ""


def resolve(order: StoreOrder, maps: Mapping[str, SkuMap], catalog: Mapping[str, Mapping]) -> Resolution:
    """Our order lines for a store order. ``catalog``: sku -> {material, color, niche, name, active}."""
    lines: list[dict] = []
    problems: list[Problem] = []
    ignored: list[str] = []
    for line in order.lines:
        m = maps.get(line.key)
        if m is None and line.sku.strip() in catalog and catalog[line.sku.strip()].get("active", True):
            m = SkuMap("catalog", sku=line.sku.strip())          # the store already uses our SKU
        if m is None:
            problems.append(Problem("unmapped", line.key, line.title, f"{line.title}: which product is this?"))
            continue
        if m.product == "ignore":
            ignored.append(line.title)
            continue
        if not 1 <= line.quantity <= MAX_QUANTITY:
            problems.append(Problem("problem", line.key, line.title,
                                    f"{line.title}: {line.quantity} units on one line (at most {MAX_QUANTITY})"))
            continue
        base = {"quantity": line.quantity, "unit_price_agorot": max(0, line.unit_price_agorot)}
        if m.product == "catalog":
            p = catalog.get(m.sku)
            if p is None or not p.get("active", True):
                why = "not in the catalog" if p is None else "switched off"
                problems.append(Problem("problem", line.key, line.title, f"{line.title}: catalog SKU {m.sku} is {why}"))
                continue
            lines.append({**base, "product": "catalog", "sku": m.sku, "material": p["material"], "color": p["color"],
                          "text": (str(p.get("name") or m.sku))[:40], "niche": p.get("niche") or ""})
        else:
            text = _prop(line.properties, m.text_property)
            if PRODUCTS[m.product].needs_text and not text:
                problems.append(Problem("problem", line.key, line.title,
                                        f"{line.title}: the customer's \"{m.text_property}\" is missing"))
                continue
            lines.append({**base, "product": m.product, "text": text[:40], "material": m.material, "color": m.color})
    if len(lines) > MAX_LINES:
        problems.append(Problem("problem", "", order.name, f"{len(lines)} lines (an order holds at most {MAX_LINES})"))
    return Resolution(tuple(lines), tuple(problems), tuple(ignored))


def decide(order: StoreOrder, res: Resolution, *, previous: str | None, only_paid: bool,
           since: datetime | None = None) -> str:
    """import | needs_mapping | waiting_payment | cancelled | skipped | imported | old.

    ``previous``: the status we stored last time (None: first seen). Imported and skipped orders stay so (an
    imported one cancelled at the store is the caller's to report, not to undo); an order placed before ``since``
    is not ours to import (the store's history before the connector was switched on)."""
    if previous in ("imported", "skipped"):
        return previous
    if order.cancelled:
        return "cancelled"
    if previous is None and since and order.placed_at and order.placed_at < since:
        return "old"
    if only_paid and not order.paid:
        return "waiting_payment"
    if res.problems or not res.lines:
        return "needs_mapping"
    return "import"


def order_id(prefix: str, external_id: str) -> str:
    """Our order id for a store order: fixed by the store's id (a repeat is the same order)."""
    clean = "".join(ch for ch in external_id if ch.isalnum() or ch in "-_")
    return f"{prefix}-{clean}"[:40]


def ready(order: StoreOrder, res: Resolution, *, oid: str, connector_id: str, channel: str) -> dict:
    """The StoreOrderReady fields: our lines, the store's shipping and discount, and the payment when it is paid.

    Our order's total always equals what the customer paid at the store, so the books never show an over- or
    under-payment: money the store charged beyond our lines (a line we do not print, e.g. gift wrap; tax the store
    added on top; a tip) is added to shipping as "extras", named in the notes; a store total below ours (rounding,
    a discount on lines we left out) becomes discount."""
    subtotal = sum(line["quantity"] * line["unit_price_agorot"] for line in res.lines)
    discount = min(max(0, order.discount_agorot), subtotal)
    shipping = max(0, order.shipping_agorot)
    notes = [f"{channel} {order.name}"]
    gap = order.total_agorot - (subtotal - discount + shipping) if order.total_agorot > 0 else 0
    if gap > 0:
        shipping += gap
        extras = ", ".join(sorted(set(res.ignored)))[:80]
        notes.append(f"₪{gap / 100:.2f} store extras in shipping" + (f" ({extras})" if extras else ""))
    elif gap < 0:
        discount = min(subtotal, discount - gap)
    if order.note:
        notes.append(order.note)
    return {"order_id": oid, "connector_id": connector_id, "channel": channel, "external_name": order.name[:60],
            "customer_name": (order.customer_name or order.name or "Store customer")[:80],
            "customer_ref": order.customer_ref[:80], "items": list(res.lines),
            "discount_agorot": discount, "shipping_agorot": shipping, "notes": " · ".join(notes)[:500],
            "paid_agorot": order.total_agorot if order.paid else 0,
            "paid_at": order.placed_at.isoformat() if order.paid and order.placed_at else None}


def problems_json(res: Resolution) -> list[dict]:
    return [asdict(p) for p in res.problems]


# ---------------------------------------------------------------- the store connector


@dataclass(frozen=True)
class Push:
    """Something to send back to the store: the tracking of a shipped order."""

    kind: str
    external_id: str
    order_id: str
    payload: Mapping


class StoreConnector(Connector):
    """A store: ``fetch`` reads orders changed since the cursor (or test orders in fake mode); ``push`` sends one
    shipment back. The agent does the rest (store_orders, the SKU map, the order, the payment)."""

    kind = "store"
    channel = ""                 # our order channel (CHANNELS)
    order_prefix = ""            # our order ids: <prefix>-<store id>

    async def fetch(self, ctx: SyncContext) -> tuple[Sequence[StoreOrder], dict]:   # pragma: no cover - per store
        raise NotImplementedError

    async def push(self, ctx: SyncContext, push: Push) -> None:                       # pragma: no cover - per store
        raise NotImplementedError

    async def sync(self, ctx: SyncContext) -> SyncResult:
        orders, cursor = await self.fetch(ctx)
        return SyncResult([Record("order", o.external_id, order_id(self.order_prefix, o.external_id), o.to_payload())
                           for o in orders], cursor)
