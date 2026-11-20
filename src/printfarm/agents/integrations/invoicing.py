"""Invoicing connectors (Wave 2 / W2-B): every payment gets its document from the invoicing provider (Morning first;
iCount or EZcount would be adapters like it), and every refund its credit document.

Pure rules here, shared by every provider:

* ``kind_for``: which document. An osek murshe (VAT registered) issues a tax invoice-receipt for a payment and a
  credit invoice for a refund; an osek patur issues a receipt, and a refund of a receipt is done in the provider by
  hand (there is no credit document for a receipt), so it is marked "manual".
* ``build``: what the document says: the customer, one line per order item when the payment pays the whole order
  (shipping as its own line, the discount spread so the lines add up), otherwise one line for the amount paid; the
  payment's method; the document it credits.

The provider numbers each document and, for a tax invoice above the threshold, gets the Israel Invoices allocation
number from the Tax Authority itself (the business connects its account in the provider once).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from printfarm.agents.integrations.framework import Connector, SyncContext, SyncResult


@dataclass(frozen=True)
class DocLine:
    description: str
    quantity: int
    unit_price_agorot: int       # gross (VAT included for an osek murshe)


@dataclass(frozen=True)
class DocRequest:
    kind: str                    # receipt | invoice_receipt | credit
    amount_agorot: int           # gross, positive
    client_name: str
    client_email: str
    lines: tuple[DocLine, ...]
    method: str                  # our PaymentMethod value
    paid_on: str                 # YYYY-MM-DD
    order_id: str | None
    payment_id: str
    vat_exempt: bool             # osek patur
    linked_external_id: str | None = None
    remarks: str = ""

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Issued:
    external_id: str
    number: str
    url: str
    allocation: str = ""


def kind_for(business_type: str, refund: bool) -> str | None:
    """receipt | invoice_receipt | credit, or None: no automatic document (a patur's refund)."""
    if business_type == "murshe":
        return "credit" if refund else "invoice_receipt"
    return None if refund else "receipt"


def _spread(amounts: Sequence[int], total: int) -> list[int]:
    """Scale ``amounts`` to add up to ``total`` exactly (largest remainder; the last line takes the rounding)."""
    base = sum(amounts)
    if base <= 0:
        return list(amounts)
    out = [a * total // base for a in amounts]
    out[-1] += total - sum(out)
    return out


def build(*, kind: str, payment: Mapping, order: Mapping | None, items: Sequence[Mapping], vat_exempt: bool,
          linked_external_id: str | None = None) -> DocRequest:
    """The document of one payment. ``payment``: amount_agorot, method, at, payment_id; ``order``: customer_name,
    customer_ref, total_agorot, shipping_agorot, discount_agorot, order_id; ``items``: label, text, quantity,
    line_total_agorot."""
    amount = int(payment["amount_agorot"])
    ref = str((order or {}).get("customer_ref") or "")
    email = ref if "@" in ref and " " not in ref else ""
    name = str((order or {}).get("customer_name") or "Customer")[:80]
    oid = (order or {}).get("order_id")
    lines: list[DocLine] = []
    whole = order is not None and items and amount == int(order.get("total_agorot") or 0)
    if whole:
        totals = [int(i["line_total_agorot"]) for i in items]
        shipping = int(order.get("shipping_agorot") or 0)
        # the discount is spread over the item lines so every line is a real price and they add up to the payment
        item_totals = _spread(totals, amount - shipping) if sum(totals) else totals
        for i, t in zip(items, item_totals, strict=True):
            q = max(1, int(i.get("quantity") or 1))
            label = f"{i.get('label') or i.get('product') or 'Item'}" + (f" “{i['text']}”" if i.get("text") else "")
            if t % q == 0:
                lines.append(DocLine(label[:120], q, t // q))
            else:                                               # not divisible: one line for the whole quantity
                lines.append(DocLine(f"{label} ×{q}"[:120], 1, t))
        if shipping:
            lines.append(DocLine("Shipping", 1, shipping))
    else:
        lines.append(DocLine(f"Order {oid}" if oid else "Payment", 1, amount))
    at = payment.get("at")
    paid_on = at.date().isoformat() if hasattr(at, "date") else str(at or "")[:10]
    return DocRequest(kind=kind, amount_agorot=amount, client_name=name, client_email=email, lines=tuple(lines),
                      method=str(payment.get("method") or "unknown"), paid_on=paid_on, order_id=oid,
                      payment_id=str(payment["payment_id"]), vat_exempt=vat_exempt,
                      linked_external_id=linked_external_id,
                      remarks=f"Order {oid}" if oid else "")


class InvoicingConnector(Connector):
    """An invoicing provider: ``issue`` one document. Its scheduled sync only checks the connection."""

    kind = "invoicing"
    default_interval_s = 3600

    async def issue(self, ctx: SyncContext, req: DocRequest, number_hint: int) -> Issued:   # pragma: no cover
        raise NotImplementedError

    async def check(self, ctx: SyncContext) -> None:                                        # pragma: no cover
        raise NotImplementedError

    async def sync(self, ctx: SyncContext) -> SyncResult:
        await self.check(ctx)
        return SyncResult([], dict(ctx.cursor))
