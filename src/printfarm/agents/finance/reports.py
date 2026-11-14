"""Finance reports (pure, no I/O): periods, profit & loss, product margins and VAT. All money is integer agorot, net
of VAT unless a name says gross.

Profit & loss counts income (payments, refunds negative) and expense rows of kind "cash" and "estimated"; "allocated"
rows (per-job filament and electricity) only feed product margins, so filament is never counted twice.

Electricity: when a period has an electricity bill (a cash or estimated electricity expense), the bill counts.
Otherwise the per-job electricity of the period (print hours × watts × tariff) stands in as an estimate, flagged.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Period:
    kind: str           # month | quarter | year | vat (two months, as Israeli VAT returns)
    start: date
    end: date           # exclusive
    label: str


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def period_of(kind: str, anchor: date) -> Period:
    if kind == "month":
        start = anchor.replace(day=1)
        return Period(kind, start, _add_months(start, 1), f"{MONTHS[start.month - 1]} {start.year}")
    if kind == "quarter":
        start = date(anchor.year, (anchor.month - 1) // 3 * 3 + 1, 1)
        return Period(kind, start, _add_months(start, 3), f"Q{(start.month - 1) // 3 + 1} {start.year}")
    if kind == "year":
        return Period(kind, date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1), str(anchor.year))
    if kind == "vat":
        start = date(anchor.year, (anchor.month - 1) // 2 * 2 + 1, 1)
        end = _add_months(start, 2)
        return Period(kind, start, end, f"{MONTHS[start.month - 1]}–{MONTHS[end.month - 2]} {start.year}")
    raise ValueError(f"unknown period {kind!r}")


def previous(p: Period) -> Period:
    step = {"month": 1, "quarter": 3, "year": 12, "vat": 2}[p.kind]
    return period_of(p.kind, _add_months(p.start, -step))


@dataclass(frozen=True)
class ExpenseRow:
    category: str
    kind: str           # cash | estimated | allocated
    net: int
    vat: int = 0


@dataclass(frozen=True)
class PnL:
    revenue: int
    expenses: int
    profit: int
    margin_pct: float | None
    mix: dict[str, int]                 # expenses per category (P&L view)
    electricity: int
    electricity_estimated: bool         # no bill in the period: per-job estimate, or a placeholder entered by hand
    electricity_from_jobs: bool         # the estimate comes from print hours × watts × tariff


def pnl(revenue: int, rows: Iterable[ExpenseRow]) -> PnL:
    rows = list(rows)
    booked = [r for r in rows if r.kind in ("cash", "estimated")]
    mix: dict[str, int] = {}
    for r in booked:
        if r.category != "electricity":
            mix[r.category] = mix.get(r.category, 0) + r.net
    bills = [r for r in booked if r.category == "electricity"]
    if bills:
        electricity = sum(r.net for r in bills)
        estimated, from_jobs = any(r.kind == "estimated" for r in bills), False
    else:
        electricity = sum(r.net for r in rows if r.kind == "allocated" and r.category == "electricity")
        estimated, from_jobs = True, True
    if electricity:
        mix["electricity"] = electricity
    expenses = sum(mix.values())
    profit = revenue - expenses
    return PnL(revenue, expenses, profit, round(profit / revenue * 100, 1) if revenue > 0 else None,
               dict(sorted(mix.items(), key=lambda kv: -kv[1])), electricity, estimated, from_jobs)


def change_pct(now: int, before: int) -> float | None:
    """Relative change, None when there is nothing to compare with."""
    return round((now - before) / abs(before) * 100, 1) if before else None


@dataclass
class ProductMargin:
    product_type: str
    units: int = 0
    revenue: int = 0
    costs: dict[str, int] = field(default_factory=dict)    # allocated, per category

    @property
    def cost(self) -> int:
        return sum(self.costs.values())

    @property
    def margin(self) -> int:
        return self.revenue - self.cost

    @property
    def margin_pct(self) -> float | None:
        return round(self.margin / self.revenue * 100, 1) if self.revenue > 0 else None


def product_margins(revenue: dict[str, int], allocated: Iterable[tuple[str, str, int]], units: dict[str, int]) -> list[ProductMargin]:
    """Per product type: revenue (payments), allocated costs (filament, electricity) and units shipped."""
    out: dict[str, ProductMargin] = {}
    for product, amount in revenue.items():
        out.setdefault(product, ProductMargin(product)).revenue += amount
    for product, category, amount in allocated:
        pm = out.setdefault(product, ProductMargin(product))
        pm.costs[category] = pm.costs.get(category, 0) + amount
    for product, n in units.items():
        out.setdefault(product, ProductMargin(product)).units += n
    return sorted(out.values(), key=lambda p: -p.revenue)


@dataclass(frozen=True)
class VatSummary:
    output_vat: int     # charged on income
    input_vat: int      # paid on expenses with a tax invoice
    payable: int        # to pay (negative: refund due)


def vat_summary(output_vat: int, input_vat: int) -> VatSummary:
    return VatSummary(output_vat, input_vat, output_vat - input_vat)
