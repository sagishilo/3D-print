"""Documents and parcels read side (Wave 2 / W2-B): each payment's receipt / tax invoice-receipt and each refund's
credit document (integrations.documents), each order's parcel (integrations.shipments), the printable address label
of a parcel, and the preview of a test-mode document. Changes are the commands documents.issue, shipments.create and
shipments.update, handled by the integrations agent.
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from printfarm.agents.integrations.connectors import REGISTRY
from printfarm.agents.integrations.couriers import CourierConnector

from . import auth

router = APIRouter(dependencies=[Depends(auth.OPERATOR)])
DOC_STATUSES = ("pending", "issued", "failed", "manual", "skipped")
KIND_HE = {"receipt": "קבלה", "invoice_receipt": "חשבונית מס / קבלה", "credit": "חשבונית זיכוי"}

DOC_SQL = ("SELECT d.id, d.connector_id, d.payment_id, d.order_id, d.kind, d.amount_agorot, d.status, d.number, d.url, "
           "d.allocation, d.attempts, d.error, d.requested_by, d.created_at, d.issued_at, c.mode "
           "FROM integrations.documents d JOIN integrations.connectors c USING (connector_id)")
SHIP_SQL = ("SELECT shipment_id, order_id, connector_id, carrier, service, tracking, tracking_url, label_url, status, "
            "ship_to, weight_g, events, created_by, created_at, status_at FROM integrations.shipments")


@router.get("/api/v1/documents")
async def documents(request: Request, status: str | None = None, order_id: str | None = None,
                    limit: int = Query(100, ge=1, le=500)):
    where, params = [], []
    if status in DOC_STATUSES:
        where.append("d.status = %s")
        params.append(status)
    if order_id:
        where.append("d.order_id = %s")
        params.append(order_id)
    db = request.app.state.db
    rows = await db.fetchall(DOC_SQL + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY d.id DESC LIMIT %s",
                             (*params, limit))
    counts = await db.fetchall("SELECT status, count(*) AS n FROM integrations.documents GROUP BY 1")
    # payments with no document yet (an invoicing provider was off, or a document was skipped): offered to issue
    missing = await db.fetchall(
        "SELECT p.payment_id, p.order_id, p.kind, p.amount_agorot, p.method, p.at FROM coordinator.payments p "
        "WHERE NOT EXISTS (SELECT 1 FROM integrations.documents d WHERE d.payment_id = p.payment_id) "
        "ORDER BY p.at DESC LIMIT 50")
    provider = await db.fetchall(
        "SELECT connector_id, label, enabled, mode FROM integrations.connectors WHERE kind = 'invoicing' ORDER BY label")
    return {"documents": rows, "counts": {s: 0 for s in DOC_STATUSES} | {r["status"]: int(r["n"]) for r in counts},
            "without_document": missing, "providers": provider}


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='he' dir='rtl'><head><meta charset='utf-8'><title>" + escape(title) + "</title>"
        "<style>body{font:15px system-ui,sans-serif;margin:24px;color:#111}table{border-collapse:collapse;width:100%}"
        "td,th{border-bottom:1px solid #ccc;padding:6px;text-align:start}.box{border:2px solid #111;padding:16px;"
        "max-width:420px}.big{font-size:22px;font-weight:700}.mono{font-family:monospace;font-size:20px}"
        ".note{color:#a00;font-weight:700}@media print{.noprint{display:none}}</style></head><body>" + body +
        "<p class='noprint'>Ctrl+P / ⌘P להדפסה · to print</p></body></html>")


@router.get("/api/v1/documents/{doc_id}/preview", response_class=HTMLResponse)
async def preview(doc_id: int, request: Request):
    """A test-mode document as a page (fake provider): what the real one will say. Never for a live document."""
    rows = await request.app.state.db.fetchall(DOC_SQL + " WHERE d.id = %s", (doc_id,))
    if not rows or rows[0]["mode"] != "fake" or rows[0]["status"] != "issued":
        raise HTTPException(404, "no test document")
    d = rows[0]
    req = (await request.app.state.db.fetchall("SELECT request FROM integrations.documents WHERE id = %s",
                                               (doc_id,)))[0]["request"] or {}
    lines = "".join(f"<tr><td>{escape(str(line.get('description', '')))}</td><td>{int(line.get('quantity', 1))}</td>"
                    f"<td>₪{int(line.get('unit_price_agorot', 0)) / 100:,.2f}</td></tr>" for line in req.get("lines", []))
    body = (f"<p class='note'>מסמך בדיקה (מצב בדיקה): לא מסמך מס אמיתי · TEST DOCUMENT</p>"
            f"<h1>{escape(KIND_HE.get(d['kind'], d['kind']))} {escape(d['number'] or '')}</h1>"
            f"<p>לכבוד: {escape(str(req.get('client_name', '')))} · הזמנה {escape(d['order_id'] or '—')} · "
            f"{escape(str(req.get('paid_on', '')))}</p>"
            f"<table><tr><th>פריט</th><th>כמות</th><th>מחיר</th></tr>{lines}</table>"
            f"<p class='big'>סה\"כ: ₪{int(d['amount_agorot']) / 100:,.2f}</p>")
    return _page(f"Test document {d['number']}", body)


@router.get("/api/v1/shipments")
async def shipments(request: Request, status: str | None = None, order_id: str | None = None,
                    limit: int = Query(100, ge=1, le=500)):
    where, params = [], []
    if status:
        where.append("status = %s")
        params.append(status)
    if order_id:
        where.append("order_id = %s")
        params.append(order_id)
    rows = await request.app.state.db.fetchall(
        SHIP_SQL + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY created_at DESC LIMIT %s",
        (*params, limit))
    couriers = await request.app.state.db.fetchall(
        "SELECT connector_id, label, enabled, mode FROM integrations.connectors WHERE kind = 'courier' ORDER BY label")
    return {"shipments": rows, "couriers": [{**c, "tracking_pattern": getattr(REGISTRY.get(c["connector_id"]),
                                                                              "tracking_pattern", "")} for c in couriers]}


@router.get("/api/v1/shipments/{shipment_id}/label", response_class=HTMLResponse)
async def label(shipment_id: str, request: Request):
    """A printable address label for a parcel (sender from the courier's settings, recipient from the parcel)."""
    db = request.app.state.db
    rows = await db.fetchall(SHIP_SQL + " WHERE shipment_id = %s", (shipment_id,))
    if not rows:
        raise HTTPException(404, f"no parcel {shipment_id}")
    s = rows[0]
    sender = {}
    if s["connector_id"] and isinstance(REGISTRY.get(s["connector_id"]), CourierConnector):
        c = await db.fetchall("SELECT settings FROM integrations.connectors WHERE connector_id = %s", (s["connector_id"],))
        sender = (c[0]["settings"] if c else {}) or {}
    to = s["ship_to"] or {}
    who = "".join(f"<div>{escape(str(to.get(k) or ''))}</div>" for k in ("name", "street", "city", "zip", "phone") if to.get(k))
    frm = "".join(f"<div>{escape(str(sender.get(k)))}</div>" for k in ("sender_name", "sender_street", "sender_city",
                                                                       "sender_phone") if sender.get(k))
    body = (f"<div class='box'><div>מאת / From:</div>{frm or '<div>—</div>'}<hr><div>אל / To:</div>"
            f"<div class='big'>{who or '—'}</div><hr><div>{escape(s['carrier'])} {escape(s['service'] or '')}</div>"
            f"<div class='mono'>{escape(s['tracking'] or '')}</div><div>Order {escape(s['order_id'])}</div></div>")
    return _page(f"Label {shipment_id}", body)


async def waiting_counts(db) -> dict:
    """For Today: documents that failed, and parcels whose label was made but never left."""
    rows = await db.fetchall(
        "SELECT (SELECT count(*) FROM integrations.documents WHERE status = 'failed') AS failed_documents, "
        "(SELECT count(*) FROM integrations.documents WHERE status = 'manual') AS manual_documents, "
        "(SELECT count(*) FROM integrations.shipments WHERE status = 'label_created' "
        " AND created_at < now() - interval '2 days') AS stale_labels")
    return dict(rows[0]) if rows else {}
