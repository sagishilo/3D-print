"""Unit economics (Wave 1 / M5), pure: what one piece of a product really costs, what it earns at a price, and the
price that reaches a target margin. Money is integer agorot; prices customers see are gross (VAT included for an
osek murshe, no VAT for an osek patur).

The unit cost of a piece is the sum of:

    filament      grams x net price per gram (latest purchase order of that filament, else the settings' default)
    electricity   print hours x printer watts x tariff
    failures      the share of failed attempts a good piece carries: (filament + electricity) x r / (1 - r), where
                  r is the recent failure rate (failed plates and failed QC units)
    labour        post-processing minutes x hourly rate
    parts         the bill of materials (parts, packaging, consumables) at their net unit prices
    channel fee   a percentage of the gross price the sales channel keeps (Etsy, a marketplace, card fees)
    shipping      optional, what one piece's shipping costs the business

Margin = net revenue - unit cost; margin % is of net revenue. The recommended price reaches the target margin
after the channel fee, rounded up to a price that ends in 9 (or .90 under 20 shekels).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from printfarm.agents.finance.ledger import FinanceSettings, round_agorot, split_vat

MAX_FAILURE_RATE = 0.5          # above this the history is noise (a new SKU, a bad week): capped


@dataclass(frozen=True)
class CostInputs:
    grams: float
    print_minutes: float
    agorot_per_gram: Decimal
    printer_watts: Decimal
    labour_minutes: float = 0.0
    labour_agorot_per_hour: Decimal = Decimal(0)
    parts_agorot: int = 0               # bill of materials of one piece
    failure_rate: float = 0.0           # 0..1
    channel_fee_pct: float = 0.0        # of the gross price
    shipping_agorot: int = 0


@dataclass
class UnitEconomics:
    costs: dict[str, int] = field(default_factory=dict)   # per part, agorot (channel fee at the given price)
    unit_cost: int = 0
    gross_price: int = 0
    net_price: int = 0
    margin: int = 0
    margin_pct: float | None = None
    profit_per_print_hour: int | None = None
    recommended_price: int | None = None                  # gross, for the target margin
    target_margin_pct: float | None = None
    below_target: bool = False


def fixed_costs(i: CostInputs, s: FinanceSettings) -> dict[str, int]:
    """Every cost of a piece that does not depend on its price (all but the channel fee)."""
    filament = Decimal(str(max(i.grams, 0))) * i.agorot_per_gram
    kwh = Decimal(str(max(i.print_minutes, 0))) / Decimal(60) * i.printer_watts / Decimal(1000)
    electricity = kwh * s.electricity_agorot_per_kwh
    r = min(max(i.failure_rate, 0.0), MAX_FAILURE_RATE)
    failures = (filament + electricity) * Decimal(str(r / (1 - r))) if r else Decimal(0)
    labour = Decimal(str(max(i.labour_minutes, 0))) / Decimal(60) * i.labour_agorot_per_hour
    out = {"filament": round_agorot(filament), "electricity": round_agorot(electricity), "failures": round_agorot(failures),
           "labour": round_agorot(labour), "parts": max(i.parts_agorot, 0), "shipping": max(i.shipping_agorot, 0)}
    return out


def nice_price(agorot: int) -> int:
    """Round a gross price up to one that ends in 9 shekels (49, 59, 129), or in .90 under 20 shekels."""
    if agorot <= 0:
        return 0
    if agorot < 2000:
        return max(0, math.ceil((agorot - 90) / 100)) * 100 + 90
    shekels = math.ceil(agorot / 100)
    return (shekels + (9 - shekels % 10) % 10) * 100


def recommended_price(fixed: int, target_margin_pct: float, channel_fee_pct: float, s: FinanceSettings) -> int | None:
    """The gross price whose margin after the channel fee reaches the target (None: no price can, e.g. fee plus
    target at 100% or more). With net n = P / (1 + VAT): margin = n - fixed - fee x P, so n = fixed / (1 - t - f(1+v))."""
    vat = float(s.vat_rate) if s.business_type == "murshe" else 0.0
    t, f = target_margin_pct / 100, channel_fee_pct / 100
    room = 1 - t - f * (1 + vat)
    if room <= 0:
        return None
    net = fixed / room
    return nice_price(math.ceil(net * (1 + vat)))


def unit_economics(i: CostInputs, s: FinanceSettings, *, gross_price: int, target_margin_pct: float | None = None) -> UnitEconomics:
    fixed = fixed_costs(i, s)
    fee = round_agorot(Decimal(gross_price) * Decimal(str(max(i.channel_fee_pct, 0))) / Decimal(100))
    costs = {**fixed, "channel_fee": fee}
    unit_cost = sum(costs.values())
    net, _vat = split_vat(gross_price, s)
    margin = net - unit_cost
    hours = i.print_minutes / 60 if i.print_minutes > 0 else None
    out = UnitEconomics(
        costs=costs, unit_cost=unit_cost, gross_price=gross_price, net_price=net, margin=margin,
        margin_pct=round(margin * 100 / net, 1) if net > 0 else None,
        profit_per_print_hour=round(margin / hours) if hours else None, target_margin_pct=target_margin_pct,
    )
    if target_margin_pct is not None:
        out.recommended_price = recommended_price(sum(fixed.values()), target_margin_pct, i.channel_fee_pct, s)
        out.below_target = out.margin_pct is not None and out.margin_pct < target_margin_pct
    return out


def failure_rate(plates_failed: int, plates_total: int, qc_failed: int, qc_checked: int) -> float:
    """The chance an attempt is lost: a failed plate, or a printed unit failing QC (independent)."""
    plate = plates_failed / plates_total if plates_total > 0 else 0.0
    qc = qc_failed / qc_checked if qc_checked > 0 else 0.0
    return min(1 - (1 - plate) * (1 - qc), MAX_FAILURE_RATE)


def ils(agorot: int) -> str:
    return f"₪{agorot / 100:,.2f}".replace(".00", "")


def quote_text(*, title: str, quantity: int, unit_price: int, extras: int = 0, shipping: int = 0, days: int | None = None,
               business: str = "", vat_included: bool = True) -> str:
    """A quote ready to paste into WhatsApp (Hebrew, as customers get it)."""
    total = unit_price * quantity + extras + shipping
    lines = [f"הצעת מחיר{f' · {business}' if business else ''}", "", f"*{title}*",
             f"{quantity} × {ils(unit_price)} = {ils(unit_price * quantity)}"]
    if extras:
        lines.append(f"תוספות: {ils(extras)}")
    if shipping:
        lines.append(f"משלוח: {ils(shipping)}")
    lines += ["", f"*סה\"כ לתשלום: {ils(total)}*" + (" (כולל מע\"מ)" if vat_included else "")]
    if days:
        lines.append(f"זמן הכנה משוער: {days} ימי עבודה")
    lines.append("ההצעה בתוקף ל-14 יום.")
    return "\n".join(lines)
