"""Unit economics and quotes (Wave 1 / M5). Read side only: the rules are in printfarm.agents.finance.costing (pure).

GET  /api/v1/finance/unit-economics   every product's cost per piece, margin at its price and the price for its
                                      niche's target margin (?niche=, ?channel= for that channel's fee)
POST /api/v1/pricing/quote            a one-off quote: a SKU, or grams and print minutes (a 3MF read by the catalog
                                      upload gives both), quantity, extras, margin -> price, totals, WhatsApp text
Nothing here changes state; the quote is POST because it carries a body (and needs the CSRF token like any form).
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from printfarm.agents.finance.costing import CostInputs, failure_rate, quote_text, unit_economics
from printfarm.agents.finance.ledger import FinanceSettings
from printfarm.contracts.catalog import UNASSIGNED, valid_sku
from printfarm.contracts.orders import CHANNELS

from . import auth, export
from .niches import niche_scope

router = APIRouter(dependencies=[Depends(auth.OPERATOR)])          # money: not for viewers
FAILURE_DAYS = 30
UE_COLUMNS = ["sku", "name", "niche_name", "price_agorot", "unit_cost_agorot", "filament_agorot", "electricity_agorot",
              "failures_agorot", "labour_agorot", "parts_agorot", "channel_fee_agorot", "margin_agorot", "margin_pct",
              "target_margin_pct", "profit_per_print_hour_agorot", "recommended_price_agorot", "below_target"]


async def context(db) -> dict:
    """What every cost calculation needs: settings, filament prices, the recent failure rate, BOM costs, niches."""
    row = await db.fetchrow("SELECT * FROM finance.settings WHERE id = 1")
    s = FinanceSettings.from_row(row) if row else FinanceSettings()
    prices = {(r["material"], r["color"]): Decimal(str(r["agorot_per_gram"])) for r in await db.fetchall(
        "SELECT material, color, agorot_per_gram FROM finance.filament_prices")}
    plates = await db.fetchrow(
        "SELECT count(*) FILTER (WHERE status = 'failed') AS failed, count(*) AS total FROM fleet.plates "
        "WHERE status IN ('finished', 'failed') AND finished_at > now() - make_interval(days => %s)", (FAILURE_DAYS,)) or {}
    qc = await db.fetchrow(
        "SELECT coalesce(sum(units_failed), 0) AS failed, coalesce(sum(units_checked), 0) AS checked "
        "FROM coordinator.qc_checks WHERE checked_at > now() - make_interval(days => %s)", (FAILURE_DAYS,)) or {}
    rate = failure_rate(int(plates.get("failed") or 0), int(plates.get("total") or 0), int(qc.get("failed") or 0),
                        int(qc.get("checked") or 0))
    bom = {r["sku"]: int(r["agorot"] or 0) for r in await db.fetchall(
        "SELECT b.sku, round(sum(b.qty * i.cost_agorot)) AS agorot FROM inventory.bom b "
        "JOIN inventory.items i USING (item_id) GROUP BY b.sku")}
    niches = {r["slug"]: r for r in await db.fetchall(
        "SELECT slug, name, color, target_margin_pct::float AS target_margin_pct FROM catalog.niches")}
    return {"settings": s, "prices": prices, "failure_rate": rate, "bom": bom, "niches": niches}


def _inputs(ctx: dict, *, grams: float, minutes: float, material: str, color: str, labour_minutes: float, parts: int,
            channel: str | None, shipping: int = 0) -> CostInputs:
    s: FinanceSettings = ctx["settings"]
    return CostInputs(
        grams=grams, print_minutes=minutes,
        agorot_per_gram=ctx["prices"].get((material, color), s.default_filament_agorot_per_gram),
        printer_watts=s.default_printer_watts, labour_minutes=labour_minutes,
        labour_agorot_per_hour=s.labour_agorot_per_hour, parts_agorot=parts, failure_rate=ctx["failure_rate"],
        channel_fee_pct=float(s.channel_fees.get(channel, 0)) if channel else 0.0, shipping_agorot=shipping)


def _channel(channel: str | None) -> str | None:
    if channel is not None and channel not in CHANNELS:
        raise HTTPException(400, f"unknown channel {channel!r}")
    return channel


@router.get("/api/v1/finance/unit-economics")
async def unit_economics_report(request: Request, niche: str | None = None, channel: str | None = Query(None),
                               format: str | None = Query(None, pattern=export.FORMAT)):
    db, scope, channel = request.app.state.db, niche_scope(niche), _channel(channel)
    ctx = await context(db)
    rows = await db.fetchall(
        "SELECT sku, name, niche, material, color, price_agorot, est_grams, est_minutes, estimated, post_minutes "
        "FROM catalog.products WHERE active" + (" AND niche = %s" if scope else "") + " ORDER BY name",
        (scope,) if scope else ())
    out = []
    for p in rows:
        n = ctx["niches"].get(p["niche"]) or ctx["niches"].get(UNASSIGNED) or {}
        i = _inputs(ctx, grams=float(p["est_grams"]), minutes=float(p["est_minutes"]), material=p["material"],
                    color=p["color"], labour_minutes=float(p["post_minutes"] or 0), parts=ctx["bom"].get(p["sku"], 0),
                    channel=channel)
        e = unit_economics(i, ctx["settings"], gross_price=int(p["price_agorot"]),
                           target_margin_pct=n.get("target_margin_pct"))
        out.append({"sku": p["sku"], "name": p["name"], "niche": p["niche"], "niche_name": n.get("name", p["niche"]),
                    "niche_color": n.get("color", 8), "estimated": p["estimated"], "grams": p["est_grams"],
                    "print_minutes": p["est_minutes"], "post_minutes": p["post_minutes"], **asdict(e)})
    if format:
        flat = [{**r, **{f"{k}_agorot": v for k, v in r["costs"].items()}, "price_agorot": r["gross_price"],
                 "unit_cost_agorot": r["unit_cost"], "margin_agorot": r["margin"],
                 "profit_per_print_hour_agorot": r["profit_per_print_hour"], "recommended_price_agorot": r["recommended_price"]}
                for r in out]
        return export.table(flat, UE_COLUMNS, name=f"printfarm-unit-economics{'-' + channel if channel else ''}", fmt=format,
                            sheet="Unit economics")
    s: FinanceSettings = ctx["settings"]
    return {"products": out, "failure_rate": round(ctx["failure_rate"], 4), "failure_days": FAILURE_DAYS,
            "channel": channel, "channel_fee_pct": float(s.channel_fees.get(channel, 0)) if channel else 0.0,
            "labour_agorot_per_hour": float(s.labour_agorot_per_hour), "below_target": sum(1 for x in out if x["below_target"])}


class QuoteIn(BaseModel):
    """A one-off quote. A SKU brings its grams, minutes, filament, labour and parts; otherwise give them."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=120)
    sku: str | None = None
    grams: float | None = Field(default=None, gt=0, le=20_000)
    print_minutes: float | None = Field(default=None, gt=0, le=100_000)
    material: str = Field(default="PLA", max_length=20)
    color: str = Field(default="black", max_length=20)
    labour_minutes: float | None = Field(default=None, ge=0, le=6000)
    parts_agorot: int | None = Field(default=None, ge=0, le=10_000_000)
    quantity: int = Field(default=1, ge=1, le=1000)
    extras_agorot: int = Field(default=0, ge=0, le=100_000_000)        # design work, rush fee (gross, whole order)
    shipping_agorot: int = Field(default=0, ge=0, le=10_000_000)       # charged to the customer (gross, whole order)
    channel: str | None = None
    niche: str | None = None
    target_margin_pct: float | None = Field(default=None, ge=0, lt=95)
    unit_price_agorot: int | None = Field(default=None, ge=0, le=100_000_000)   # a price to check instead
    days: int | None = Field(default=None, ge=1, le=365)


@router.post("/api/v1/pricing/quote")
async def quote(body: QuoteIn, request: Request, _c: dict = Depends(auth.OPERATOR_CHANGE)):
    db = request.app.state.db
    ctx = await context(db)
    channel = _channel(body.channel)
    product = None
    if body.sku:
        if not valid_sku(body.sku):
            raise HTTPException(400, "bad SKU")
        product = await db.fetchrow(
            "SELECT sku, name, niche, material, color, price_agorot, est_grams, est_minutes, post_minutes "
            "FROM catalog.products WHERE sku = %s", (body.sku,))
        if product is None:
            raise HTTPException(404, f"no SKU {body.sku}")
    grams = body.grams or (float(product["est_grams"]) if product else None)
    minutes = body.print_minutes or (float(product["est_minutes"]) if product else None)
    if not grams or not minutes:
        raise HTTPException(422, "give the grams and print minutes, or choose a product")
    niche = niche_scope(body.niche) or (product["niche"] if product else UNASSIGNED)
    n = ctx["niches"].get(niche) or {}
    target = body.target_margin_pct if body.target_margin_pct is not None else n.get("target_margin_pct", 50.0)
    i = _inputs(ctx, grams=grams, minutes=minutes, material=product["material"] if product else body.material,
                color=product["color"] if product else body.color,
                labour_minutes=body.labour_minutes if body.labour_minutes is not None
                else float(product["post_minutes"] or 0) if product else 0.0,
                parts=body.parts_agorot if body.parts_agorot is not None else ctx["bom"].get(body.sku or "", 0),
                channel=channel)
    probe = unit_economics(i, ctx["settings"], gross_price=0, target_margin_pct=target)
    price = body.unit_price_agorot if body.unit_price_agorot is not None else (probe.recommended_price or 0)
    e = unit_economics(i, ctx["settings"], gross_price=price, target_margin_pct=target)
    s: FinanceSettings = ctx["settings"]
    title = body.title.strip() or (product["name"] if product else "הדפסה בהזמנה אישית")
    total = price * body.quantity + body.extras_agorot + body.shipping_agorot
    return {
        **asdict(e), "recommended_price": probe.recommended_price, "unit_price": price, "quantity": body.quantity,
        "extras_agorot": body.extras_agorot, "shipping_agorot": body.shipping_agorot, "total_agorot": total,
        "total_margin_agorot": e.margin * body.quantity, "print_hours": round(minutes * body.quantity / 60, 1),
        "grams": grams, "print_minutes": minutes, "sku": product["sku"] if product else None, "niche": niche,
        "failure_rate": round(ctx["failure_rate"], 4), "title": title,
        "text": quote_text(title=title, quantity=body.quantity, unit_price=price, extras=body.extras_agorot,
                           shipping=body.shipping_agorot, days=body.days, vat_included=s.business_type == "murshe"),
    }
