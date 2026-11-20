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

from printfarm.contracts.catalog import NAMEPLATE_NICHE, NICHE_RE, SKU_RE, STOCK_CHANNEL, UNASSIGNED

MAX_ITEMS = 20
CHANNELS = ("manual", "whatsapp", "facebook", "instagram", "etsy", "shopify", "market", "website", "api", STOCK_CHANNEL)


@dataclass(frozen=True)
class Product:
    key: str
    label: str
    default_price_agorot: int
    needs_text: bool


# A personalized nameplate (OpenSCAD, one model per line), or a ready-made catalog SKU (a 3MF template; the line names
# its ``sku`` and carries the catalog's price, which the gateway fills in from the catalog).
PRODUCTS: dict[str, Product] = {
    "nameplate": Product("nameplate", "Nameplate", 4500, needs_text=True),
    "catalog": Product("catalog", "Catalog item", 0, needs_text=False),
}


def product_type(product: str, sku: str = "") -> str:
    """What Finance books a line's money and costs under: the SKU of a catalog line, else the product."""
    return sku if product == "catalog" and sku else product


def line_niche(line: OrderLine) -> str:
    """The niche a line is sold under: as the gateway resolved it from the catalog; a nameplate is a personalised
    gift; anything else without one is unassigned."""
    if line.niche:
        return line.niche
    return NAMEPLATE_NICHE if line.product == "nameplate" else UNASSIGNED

# Board columns in order (NEEDS_ATTENTION cards show in the column they came from).
STAGES = ("LEAD", "RECEIVED", "PERSONALIZING", "SCHEDULING", "PRINTING", "QC", "SHIPPED")
OPEN_STAGES = (*STAGES[:-1], "NEEDS_ATTENTION")
TERMINAL_STAGES = ("SHIPPED", "CANCELLED")


class OrderLine(BaseModel):
    """One line of an order: ``quantity`` units of a product (each unit is printed as its own job)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product: str = "nameplate"
    sku: str = Field(default="", max_length=32)          # catalog lines only
    text: str = Field(default="", max_length=40)
    material: str = Field(default="PLA", max_length=20)
    color: str = Field(default="black", max_length=20)
    quantity: int = Field(default=1, ge=1, le=50)
    unit_price_agorot: int | None = Field(default=None, ge=0, le=10_000_000)   # None = the product's default
    niche: str = Field(default="", max_length=32)        # filled by the gateway from the catalog (a snapshot)

    @model_validator(mode="after")
    def _known_product(self) -> OrderLine:
        p = PRODUCTS.get(self.product)
        if p is None:
            raise ValueError(f"unknown product {self.product!r}")
        if p.needs_text and not self.text.strip():
            raise ValueError(f"a {p.label.lower()} needs its text")
        if self.product == "catalog":
            if not SKU_RE.match(self.sku):
                raise ValueError(f"a catalog item needs a SKU, not {self.sku!r}")
            if self.unit_price_agorot is None:
                raise ValueError(f"catalog item {self.sku}: the price comes from the catalog")
        elif self.sku:
            raise ValueError("only a catalog item has a SKU")
        if self.niche and not NICHE_RE.match(self.niche):
            raise ValueError(f"bad niche {self.niche!r}")
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
