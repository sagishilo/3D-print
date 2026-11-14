"""Orders v2: line items, prices and the pipeline stages, shared by the coordinator (authoritative), the gateway and
the dashboard (preview). Prices are gross agorot (VAT included, as customers see them); Finance splits VAT.

Stages (coordinator saga states) and the moves an operator may make on the board:

    LEAD --confirm--> RECEIVED --dispatch--> PERSONALIZING --dispatch (models ready)--> SCHEDULING
    SCHEDULING --(printer starts a unit)--> PRINTING --(all units printed)--> QC --pass, then ship--> SHIPPED
    NEEDS_ATTENTION --retry--> PERSONALIZING (model failed) | SCHEDULING (units failed)
    any open stage without a running print --cancel--> CANCELLED
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ITEMS = 20
CHANNELS = ("manual", "whatsapp", "facebook", "instagram", "etsy", "market", "website", "api")


@dataclass(frozen=True)
class Product:
    key: str
    label: str
    default_price_agorot: int
    needs_text: bool


# Phase 8 adds catalog SKUs; until then every printable item is a personalized nameplate.
PRODUCTS: dict[str, Product] = {
    "nameplate": Product("nameplate", "Nameplate", 4500, needs_text=True),
}

# Board columns in order (NEEDS_ATTENTION cards show in the column they came from).
STAGES = ("LEAD", "RECEIVED", "PERSONALIZING", "SCHEDULING", "PRINTING", "QC", "SHIPPED")
OPEN_STAGES = (*STAGES[:-1], "NEEDS_ATTENTION")
TERMINAL_STAGES = ("SHIPPED", "CANCELLED")


class OrderLine(BaseModel):
    """One line of an order: ``quantity`` units of a product (each unit is printed as its own job)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product: str = "nameplate"
    text: str = Field(default="", max_length=40)
    material: str = Field(default="PLA", max_length=20)
    color: str = Field(default="black", max_length=20)
    quantity: int = Field(default=1, ge=1, le=50)
    unit_price_agorot: int | None = Field(default=None, ge=0, le=10_000_000)   # None = the product's default

    @model_validator(mode="after")
    def _known_product(self) -> OrderLine:
        p = PRODUCTS.get(self.product)
        if p is None:
            raise ValueError(f"unknown product {self.product!r}")
        if p.needs_text and not self.text.strip():
            raise ValueError(f"a {p.label.lower()} needs its text")
        return self

    @property
    def unit_price(self) -> int:
        return self.unit_price_agorot if self.unit_price_agorot is not None else PRODUCTS[self.product].default_price_agorot


@dataclass(frozen=True)
class Pricing:
    lines: tuple[int, ...]      # line totals
    subtotal: int
    discount: int
    shipping: int
    total: int                  # gross, what the customer pays
    units: int


def price_order(lines: list[OrderLine], *, discount_agorot: int = 0, shipping_agorot: int = 0) -> Pricing:
    """Totals of an order. Raises ValueError for an empty order or a discount above the subtotal."""
    if not lines:
        raise ValueError("an order needs at least one item")
    if len(lines) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} items per order")
    if discount_agorot < 0 or shipping_agorot < 0:
        raise ValueError("discount and shipping cannot be negative")
    totals = tuple(line.unit_price * line.quantity for line in lines)
    subtotal = sum(totals)
    if discount_agorot > subtotal:
        raise ValueError("the discount is larger than the items' total")
    return Pricing(totals, subtotal, discount_agorot, shipping_agorot, subtotal - discount_agorot + shipping_agorot,
                   sum(line.quantity for line in lines))
