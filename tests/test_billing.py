"""W2-B, pure parts: which document a payment gets (murshe / patur, payment / refund), what it says (lines that add
up to the payment, shipping, discount spread), Morning's request body and answer, its token and errors (no network),
Israel Post's tracking numbers, links, test labels and test tracking, a parcel's moves, the gateway's commands and
Today's items."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from printfarm.agents.integrations import couriers, invoicing
from printfarm.agents.integrations.framework import SyncContext, SyncError, clean_settings
from printfarm.agents.integrations.israelpost import IsraelPost
from printfarm.agents.integrations.morning import Morning, body, parse
from printfarm.contracts.messages import CreateShipment

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
ORDER = {"order_id": "ord-1", "customer_name": "Noa Levi", "customer_ref": "noa@example.com", "total_agorot": 12300,
         "shipping_agorot": 2500, "discount_agorot": 1000}
ITEMS = [{"label": "Nameplate", "text": "Dan", "quantity": 2, "line_total_agorot": 9800},
         {"label": "Spiral vase", "text": "", "quantity": 1, "line_total_agorot": 1000}]


def pay(amount=12300, **kw):
    return {"payment_id": "p1", "amount_agorot": amount, "method": "bit", "at": NOW, **kw}


def ctx(mode="fake", settings=None, secrets=None):
    return SyncContext(mode=mode, cursor={}, settings=settings or {}, secrets=secrets or {}, now=NOW, log=lambda m: None)


def test_which_document():
    assert invoicing.kind_for("murshe", False) == "invoice_receipt"
    assert invoicing.kind_for("murshe", True) == "credit"
    assert invoicing.kind_for("patur", False) == "receipt"
    assert invoicing.kind_for("patur", True) is None          # a receipt has no credit document: by hand


def test_a_whole_payment_lists_the_items_and_adds_up():
    r = invoicing.build(kind="invoice_receipt", payment=pay(), order=ORDER, items=ITEMS, vat_exempt=False)
    assert (r.client_name, r.client_email, r.paid_on, r.order_id, r.remarks) == (
        "Noa Levi", "noa@example.com", "2026-11-20", "ord-1", "Order ord-1")
    # the ₪10 discount is spread over the items: 9800+1000 -> 9800 of 10800 ... adds up to 12300 - 2500
    assert [(x.description, x.quantity, x.unit_price_agorot) for x in r.lines] == [
        ("Nameplate “Dan”", 2, 4446), ("Spiral vase", 1, 908), ("Shipping", 1, 2500)]
    assert sum(x.quantity * x.unit_price_agorot for x in r.lines) == 12300


def test_lines_that_do_not_divide_stay_whole():
    order = {**ORDER, "total_agorot": 10000, "shipping_agorot": 0}
    r = invoicing.build(kind="invoice_receipt", payment=pay(10000), order=order,
                        items=[{"label": "Nameplate", "text": "", "quantity": 3, "line_total_agorot": 10000}], vat_exempt=False)
    assert [(x.description, x.quantity, x.unit_price_agorot) for x in r.lines] == [("Nameplate ×3", 1, 10000)]


def test_a_partial_payment_is_one_line_and_a_phone_is_not_an_email():
    r = invoicing.build(kind="receipt", payment=pay(5000), order={**ORDER, "customer_ref": "050 1234567"}, items=ITEMS,
                        vat_exempt=True)
    assert [(x.description, x.unit_price_agorot) for x in r.lines] == [("Order ord-1", 5000)]
    assert r.client_email == "" and r.vat_exempt
    lone = invoicing.build(kind="receipt", payment=pay(700, at="2026-11-01T09:00:00+00:00"), order=None, items=[],
                           vat_exempt=True)
    assert (lone.lines[0].description, lone.paid_on, lone.client_name) == ("Payment", "2026-11-01", "Customer")


def test_morning_bodies():
    inv = invoicing.build(kind="invoice_receipt", payment=pay(), order=ORDER, items=ITEMS, vat_exempt=False)
    b = body(inv, lang="he", email_customer=False)
    assert (b["type"], b["currency"], b["client"]) == (320, "ILS", {"name": "Noa Levi", "emails": [], "add": False})
    assert b["income"][0] == {"description": "Nameplate “Dan”", "quantity": 2, "price": 44.46, "currency": "ILS", "vatType": 1}
    assert b["payment"] == [{"type": 10, "price": 123.0, "currency": "ILS", "date": "2026-11-20"}]
    assert body(inv, lang="he", email_customer=True)["client"]["emails"] == ["noa@example.com"]
    receipt = body(invoicing.build(kind="receipt", payment=pay(5000, method="cash"), order=ORDER, items=ITEMS,
                                   vat_exempt=True), lang="he", email_customer=False)
    assert receipt["type"] == 400 and "income" not in receipt and receipt["payment"][0]["type"] == 1
    credit = body(invoicing.build(kind="credit", payment=pay(), order=ORDER, items=ITEMS, vat_exempt=False,
                                  linked_external_id="doc-9"), lang="en", email_customer=False)
    assert credit["type"] == 330 and "payment" not in credit and credit["linkedDocumentIds"] == ["doc-9"]
    assert credit["lang"] == "en"


def test_morning_answers():
    assert parse({"id": "a1", "number": 20001, "url": {"he": "https://x/he.pdf", "origin": "https://x/o.pdf"},
                  "allocationNumber": "123456789"}) == invoicing.Issued("a1", "20001", "https://x/he.pdf", "123456789")
    assert parse({"id": "a2", "number": 5, "url": {"origin": "https://x/o.pdf"}}).url == "https://x/o.pdf"
    with pytest.raises(SyncError, match="without a document id"):
        parse({"number": 1})


class FakeHttp:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def post(self, url, **kw):
        self.calls.append((url, kw))
        status, data = self.answers.pop(0)
        return httpx.Response(status, json=data, request=httpx.Request("POST", url))


def test_morning_issues_live_in_the_sandbox_first():
    http = FakeHttp((200, {"token": "jwt", "expires": 4102444800}), (200, {"id": "d1", "number": 30001, "url": {"he": "u"}}),
                    (200, {"id": "d2", "number": 30002, "url": {"he": "u2"}}))
    m = Morning(client=http)
    req = invoicing.build(kind="invoice_receipt", payment=pay(), order=ORDER, items=ITEMS, vat_exempt=False)
    secrets = {"MORNING_API_KEY_ID": "id", "MORNING_API_KEY_SECRET": "sec"}
    out = asyncio.run(m.issue(ctx("live", secrets=secrets), req, 7))
    assert out == invoicing.Issued("d1", "30001", "u")
    assert http.calls[0] == ("https://sandbox.d.greeninvoice.co.il/api/v1/account/token", {"json": {"id": "id", "secret": "sec"}})
    assert http.calls[1][0] == "https://sandbox.d.greeninvoice.co.il/api/v1/documents"
    assert http.calls[1][1]["headers"] == {"Authorization": "Bearer jwt"}
    asyncio.run(m.issue(ctx("live", secrets=secrets), req, 8))           # the token is reused
    assert len(http.calls) == 3
    prod = FakeHttp((200, {"token": "t", "expires": 4102444800}), (200, {"id": "p", "number": 1, "url": {}}))
    asyncio.run(Morning(client=prod).issue(ctx("live", settings={"environment": "production"}, secrets=secrets), req, 1))
    assert prod.calls[1][0] == "https://api.greeninvoice.co.il/api/v1/documents"


def test_morning_errors_never_show_the_secret():
    for answers, msg in ((((401, {}),), "did not give a token"),
                         (((200, {"token": "t", "expires": 4102444800}), (400, {"errorMessage": "Client name missing"})),
                          "HTTP 400: Client name missing"),
                         (((200, {"token": "t", "expires": 4102444800}), (401, {})), "refused the token")):
        m = Morning(client=FakeHttp(*answers))
        with pytest.raises(SyncError, match=msg) as exc:
            asyncio.run(m.issue(ctx("live", secrets={"MORNING_API_KEY_ID": "id", "MORNING_API_KEY_SECRET": "s3cr3t"}),
                                invoicing.build(kind="receipt", payment=pay(), order=None, items=[], vat_exempt=True), 1))
        assert "s3cr3t" not in str(exc.value)


def test_morning_test_mode_numbers_documents_and_can_be_told_to_fail():
    m = Morning()
    req = invoicing.build(kind="receipt", payment=pay(), order=None, items=[], vat_exempt=True)
    assert asyncio.run(m.issue(ctx(), req, 12)) == invoicing.Issued("fake-12", "50012", "/api/v1/documents/12/preview")
    with pytest.raises(SyncError, match="refuse"):
        asyncio.run(m.issue(ctx(settings={"fail": True}), req, 12))
    assert m.live_missing({}, {}) == ["MORNING_API_KEY_ID", "MORNING_API_KEY_SECRET"]
    with pytest.raises(ValueError):
        clean_settings(m.settings, {"environment": "staging"})


def test_israel_post_tracking_numbers_links_and_test_parcels():
    ip = IsraelPost()
    assert ip.valid_tracking("RR123456789IL") and ip.valid_tracking(" rr123456789il ") and ip.valid_tracking("1234567890")
    assert not ip.valid_tracking("RR12345IL") and not ip.valid_tracking("hello")
    assert ip.tracking_url("RR123456789IL") == "https://israelpost.co.il/itemtrace?itemcode=RR123456789IL"
    label = asyncio.run(ip.create_label(ctx(), "ord-1", {}, None, "registered", 42))
    assert label.tracking == "RR900000042IL" and ip.valid_tracking(label.tracking) and label.tracking_url
    assert asyncio.run(ip.create_label(ctx("live"), "ord-1", {}, None, "registered", 42)) is None   # by hand
    first = asyncio.run(ip.track(ctx(), label.tracking, "label_created", 0))
    assert couriers.latest(first, "label_created").status == "in_transit"
    second = asyncio.run(ip.track(ctx(), label.tracking, "in_transit", 1))
    assert couriers.latest(second, "in_transit").status == "delivered"
    assert asyncio.run(ip.track(ctx("live"), label.tracking, "in_transit", 5)) == []
    assert ip.live_missing({}, {}) == []                  # live needs no secret: the operator types the number


def test_a_parcel_only_moves_on():
    assert couriers.can_move("label_created", "in_transit") is None
    assert couriers.can_move("label_created", "cancelled") is None
    assert couriers.can_move("in_transit", "cancelled") == "only a label that has not left can be cancelled"
    assert couriers.can_move("delivered", "returned") == "it is delivered: nothing more happens to it"
    assert couriers.can_move("in_transit", "label_created") == "it is in transit already"
    assert couriers.can_move("in_transit", "in_transit") == "it is already in transit"
    assert couriers.can_move("in_transit", "returned") is None
    ev = [couriers.TrackEvent("delivered", "2026-11-20T10:00:00"), couriers.TrackEvent("in_transit", "2026-11-19T10:00:00")]
    assert couriers.latest(ev, "label_created").status == "delivered"
    assert couriers.latest([couriers.TrackEvent("in_transit", "x")], "delivered") is None
    assert couriers.shipment_id("ord-1", 2) == "pcl-ord-1-2"


def test_shipment_links_must_be_https():
    with pytest.raises(ValueError, match="https"):
        CreateShipment(order_id="o", carrier="Friend", tracking_url="javascript:alert(1)")
    assert CreateShipment(order_id="o", carrier="Friend", tracking_url="https://x.example/t/1").tracking_url


def _gateway():
    import gateway_support  # noqa: F401 - sets the gateway's environment

    from gateway.app import commands, today
    return commands, today


def test_the_gateway_knows_the_billing_commands_and_today_tells():
    commands, today = _gateway()
    for name in ("documents.issue", "shipments.create", "shipments.update"):
        assert commands.COMMANDS[name].min_role == "operator"
    assert today.billing({}) == []
    items = today.billing({"failed_documents": 2, "manual_documents": 1, "stale_labels": 1})
    assert [(i.id, i.title) for i in items] == [
        ("docs:failed_documents", "2 documents could not be issued"),
        ("docs:manual_documents", "1 refund needs a document by hand"),
        ("parcels:stale", "1 parcel label made over 2 days ago, not sent yet")]
    assert items[0].visible_to == "operator" and items[2].action["to"] == "/orders"
