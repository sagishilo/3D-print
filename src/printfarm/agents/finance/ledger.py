"""Ledger rules (pure, no I/O). All money is integer agorot.

Three kinds of expense row, so nothing is counted twice:
    cash       real money out: filament purchases (POs), manual expenses      -> profit & loss
    estimated  a placeholder until the real bill arrives (e.g. electricity)   -> profit & loss
    allocated  per-job filament + electricity, for product margins ONLY       -> never in profit & loss
Filament enters P&L once, when the spools are bought; the allocated rows only spread that cost over products.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from printfarm.contracts.catalog import UNASSIGNED
from printfarm.contracts.messages import JobShare, PaymentLine


@dataclass(frozen=True)
class FinanceSettings:
    business_type: str = "murshe"                         # "murshe" (VAT registered) | "patur" (no VAT)
    vat_rate: Decimal = Decimal("0.18")
    electricity_agorot_per_kwh: Decimal = Decimal("64")
    default_printer_watts: Decimal = Decimal("150")
    default_filament_agorot_per_gram: Decimal = Decimal("8.5")

    @classmethod
    def from_row(cls, row) -> FinanceSettings:
        return cls(
            business_type=row["business_type"],
            vat_rate=Decimal(row["vat_rate"]),
            electricity_agorot_per_kwh=Decimal(row["electricity_agorot_per_kwh"]),
            default_printer_watts=Decimal(row["default_printer_watts"]),
            default_filament_agorot_per_gram=Decimal(row["default_filament_agorot_per_gram"]),
        )


def round_agorot(x: Decimal) -> int:
    """Round half away from zero, so a refund mirrors its payment exactly."""
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def split_vat(gross_agorot: int, s: FinanceSettings) -> tuple[int, int]:
    """(net, vat) of a gross amount. An osek patur neither charges nor reclaims VAT."""
    if s.business_type != "murshe":
        return gross_agorot, 0
    net = round_agorot(Decimal(gross_agorot) / (1 + s.vat_rate))
    return net, gross_agorot - net


def business_day(when: datetime | None, tz: str, today: date | None = None) -> date:
    """The calendar day of an instant in the business's time zone (a payment at 01:00 local is that day)."""
    if when is None:
        return today or datetime.now(ZoneInfo(tz)).date()
    if when.tzinfo is None:  # naive = already local
        return when.date()
    return when.astimezone(ZoneInfo(tz)).date()


@dataclass(frozen=True)
class Allocation:
    job_id: str | None
    order_id: str | None
    product_type: str | None
    category: str            # "filament" | "electricity"
    amount_agorot: int
    note: str


def plate_allocations(
    *,
    grams_used: float,
    print_seconds: int,
    shares: Sequence[JobShare],
    material: str,
    color: str,
    succeeded: bool,
    s: FinanceSettings,
    printer_watts: Decimal | None = None,
    price_per_gram_agorot: Decimal | None = None,
) -> list[Allocation]:
    """Spread one plate's filament and electricity over its jobs, in proportion to their grams.

    A failed plate is allocated too ("scrap"): the filament was used, and the product's margin should show it.
    A plate without job shares (older messages) becomes one row without a job.
    """
    price = price_per_gram_agorot if price_per_gram_agorot is not None else s.default_filament_agorot_per_gram
    watts = printer_watts if printer_watts is not None else s.default_printer_watts
    kwh_total = Decimal(max(print_seconds, 0)) / Decimal(3600) * watts / Decimal(1000)
    electricity_total = kwh_total * s.electricity_agorot_per_kwh

    if not shares:
        shares = [JobShare(job_id="", order_id="", product_type="", grams=max(grams_used, 0.0))]
    total_grams = sum(Decimal(str(sh.grams)) for sh in shares)
    prefix = "" if succeeded else "failed plate (scrap): "

    out: list[Allocation] = []
    for sh in shares:
        grams = Decimal(str(sh.grams))
        frac = grams / total_grams if total_grams > 0 else Decimal(1) / Decimal(len(shares))
        job_id, order_id, product = sh.job_id or None, sh.order_id or None, sh.product_type or None
        filament = round_agorot(grams * price)
        if filament > 0:
            out.append(Allocation(job_id, order_id, product, "filament", filament,
                                  f"{prefix}{grams:.1f} g {material} {color}"))
        electricity = round_agorot(electricity_total * frac)
        if electricity > 0:
            out.append(Allocation(job_id, order_id, product, "electricity", electricity,
                                  f"{prefix}{kwh_total * frac:.3f} kWh"))
    return out


@dataclass(frozen=True)
class IncomeLine:
    line_no: int
    product_type: str
    niche: str
    net_agorot: int


def split_income(net_agorot: int, lines: Sequence[PaymentLine], *, product_type: str | None = None) -> list[IncomeLine]:
    """Split one income entry across the order lines it pays for, in proportion to their weights (the line totals).

    Each line is rounded; the remainder goes to the last line, so the lines always add up to the entry exactly (a
    refund splits the same way, negative). An order whose lines all weigh 0 splits evenly. Income without an order
    is one line in the "unassigned" niche, under its product type (or "other").
    """
    if not lines:
        return [IncomeLine(1, product_type or "other", UNASSIGNED, net_agorot)]
    weights = [Decimal(max(line.weight_agorot, 0)) for line in lines]
    total = sum(weights)
    if total <= 0:
        weights, total = [Decimal(1)] * len(lines), Decimal(len(lines))
    out: list[IncomeLine] = []
    for i, (line, w) in enumerate(zip(lines, weights, strict=True), start=1):
        share = round_agorot(Decimal(net_agorot) * w / total) if i < len(lines) else net_agorot - sum(x.net_agorot for x in out)
        out.append(IncomeLine(i, line.product_type, line.niche or UNASSIGNED, share))
    return out
