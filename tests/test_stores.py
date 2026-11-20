"""W2-A store connectors, pure parts: Shopify's GraphQL answer parsed into a StoreOrder, money in agorot, the SKU map
(a catalog SKU, a nameplate with its text from a line property, a line to ignore, the "map this" queue), the import
decision, the StoreOrderReady fields, connector settings, live-mode requirements, fake orders and a fake push, the
gateway's commands and queue, and Today's store item."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from printfarm.agents.integrations import stores
from printfarm.agents.integrations.framework import SyncContext, SyncError, clean_settings
from printfarm.agents.integrations.shopify import Shopify, fake_orders, parse_order, search_query
from printfarm.agents.integrations.stores import SkuMap, StoreLine, StoreOrder, agorot, decide, resolve
from printfarm.contracts.messages import MapStoreSku, StoreOrderReady

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)

NODE = {
    "id": "gid://shopify/Order/5551234", "legacyResourceId": "5551234", "name": "#1042",
    "createdAt": "2026-11-19T08:30:00Z", "updatedAt": "2026-11-19T09:00:00Z", "cancelledAt": None,
    "displayFinancialStatus": "PAID", "note": "Gift", "email": "x@example.com", "phone": None,
    "customer": {"firstName": "Noa", "lastName": "Levi", "email": "noa@example.com", "phone": None},
    "shippingAddress": {"name": "Noa Levi", "phone": "050"},
    "currentTotalPriceSet": {"shopMoney": {"amount": "113.80", "currencyCode": "ILS"}},
    "totalDiscountsSet": {"shopMoney": {"amount": "10.0"}},
    "totalShippingPriceSet": {"shopMoney": {"amount": "25.00"}},
    "lineItems": {"nodes": [
        {"sku": "NP-BLK", "title": "Nameplate", "variantTitle": "Black", "quantity": 2,
         "customAttributes": [{"key": "Name", "value": " Noa "}, {"key": "_theme", "value": "x"}],
         "originalUnitPriceSet": {"shopMoney": {"amount": "49.40"}}},
        {"sku": "", "title": "Gift wrap", "variantTitle": None, "quantity": 1, "customAttributes": [],
         "originalUnitPriceSet": {"shopMoney": {"amount": "0.995"}}},
    ]},
}

CATALOG = {"VASE-1": {"name": "Spiral vase", "material": "PETG", "color": "white", "niche": "home-decor", "active": True},
           "OLD-1": {"name": "Old", "material": "PLA", "color": "black", "niche": "", "active": False}}


def order(*lines: StoreLine, paid=True, cancelled=False, placed=NOW, **kw) -> StoreOrder:
    return StoreOrder(kw.pop("external_id", "1"), "#1", "Noa", "noa@example.com", placed, paid, "paid" if paid else "pending",
                      cancelled, tuple(lines), kw.pop("total", 10000), **kw)


def test_money_rounds_half_up_to_agorot():
    assert [agorot(x) for x in ("49.40", "0.995", "12", 3.5, None, "", "x")] == [4940, 100, 1200, 350, 0, 0, 0]


def test_a_shopify_order_is_parsed():
    o = parse_order(NODE)
    assert (o.external_id, o.name, o.customer_name, o.customer_ref, o.paid, o.cancelled) == (
        "5551234", "#1042", "Noa Levi", "noa@example.com", True, False)
    assert (o.total_agorot, o.shipping_agorot, o.discount_agorot, o.currency) == (11380, 2500, 1000, "ILS")
    assert o.placed_at == datetime(2026, 11, 19, 8, 30, tzinfo=UTC)
    assert o.lines[0].properties == {"Name": " Noa "}            # the theme's "_" properties are left out
    assert (o.lines[0].key, o.lines[1].key, o.lines[1].unit_price_agorot) == ("NP-BLK", "title:Gift wrap", 100)
    assert StoreOrder.from_payload(o.to_payload()) == o           # stored and read back unchanged
    pending = parse_order({**NODE, "displayFinancialStatus": "PENDING", "cancelledAt": "2026-11-19T10:00:00Z",
                           "customer": None, "legacyResourceId": None})
    assert (pending.paid, pending.cancelled, pending.customer_name, pending.external_id) == (False, True, "Noa Levi", "5551234")


def test_search_query_continues_from_the_cursor():
    assert search_query("2026-11-19T09:00:00Z", "2026-11-01") == "updated_at:>'2026-11-19T09:00:00Z'"
    assert search_query(None, "2026-11-01") == "created_at:>='2026-11-01'"
    assert search_query(None, None) == ""


def test_lines_resolve_through_the_map_or_wait_for_one():
    nameplate = StoreLine("NP-BLK", "Nameplate", 2, 4940, properties={"Name": " Noa "})
    vase = StoreLine("VASE-1", "Vase", 1, 9900)                    # our own SKU: no mapping needed
    wrap = StoreLine("", "Gift wrap", 1, 100)
    mystery = StoreLine("MYST", "Mystery box", 1, 500)
    maps = {"NP-BLK": SkuMap("nameplate", material="PLA", color="black"), "title:Gift wrap": SkuMap("ignore")}
    r = resolve(order(nameplate, vase, wrap, mystery), maps, CATALOG)
    assert r.lines == (
        {"quantity": 2, "unit_price_agorot": 4940, "product": "nameplate", "text": "Noa", "material": "PLA", "color": "black"},
        {"quantity": 1, "unit_price_agorot": 9900, "product": "catalog", "sku": "VASE-1", "material": "PETG",
         "color": "white", "text": "Spiral vase", "niche": "home-decor"})
    assert r.unmapped == ["MYST"]


def test_what_a_person_must_fix():
    maps = {"NP": SkuMap("nameplate", text_property="name"), "OLD": SkuMap("catalog", sku="OLD-1"),
            "GONE": SkuMap("catalog", sku="NOPE")}
    r = resolve(order(StoreLine("NP", "Nameplate", 1, 4900, properties={"Colour": "red"}),
                      StoreLine("OLD", "Old thing", 1, 100), StoreLine("GONE", "Gone", 1, 100),
                      StoreLine("NP", "Nameplate", 51, 4900, properties={"NAME": "x"})), maps, CATALOG)
    assert r.lines == () and r.unmapped == []
    assert [p.message for p in r.problems] == [
        'Nameplate: the customer\'s "name" is missing', "Old thing: catalog SKU OLD-1 is switched off",
        "Gone: catalog SKU NOPE is not in the catalog", "Nameplate: 51 units on one line (at most 50)"]
    many = resolve(order(*[StoreLine("VASE-1", "Vase", 1, 100)] * 21), {}, CATALOG)
    assert many.problems[-1].message == "21 lines (an order holds at most 20)"


def test_the_import_decision():
    ok = resolve(order(StoreLine("VASE-1", "Vase", 1, 100)), {}, CATALOG)
    waiting = resolve(order(StoreLine("MYST", "Mystery", 1, 100)), {}, CATALOG)
    o = order(StoreLine("VASE-1", "Vase", 1, 100))
    assert decide(o, ok, previous=None, only_paid=True) == "import"
    assert decide(o, waiting, previous=None, only_paid=True) == "needs_mapping"
    assert decide(order(paid=False), ok, previous=None, only_paid=True) == "waiting_payment"
    assert decide(order(paid=False), ok, previous=None, only_paid=False) == "import"
    assert decide(order(cancelled=True), ok, previous="needs_mapping", only_paid=True) == "cancelled"
    assert decide(order(cancelled=True), ok, previous="imported", only_paid=True) == "imported"    # never undone here
    assert decide(o, ok, previous="skipped", only_paid=True) == "skipped"
    assert decide(o, ok, previous=None, only_paid=True, since=NOW + timedelta(days=1)) == "old"
    assert decide(o, ok, previous="waiting_payment", only_paid=True, since=NOW + timedelta(days=1)) == "import"
    only_ignored = resolve(order(StoreLine("W", "Wrap", 1, 100)), {"W": SkuMap("ignore")}, CATALOG)
    assert decide(o, only_ignored, previous=None, only_paid=True) == "needs_mapping"


def test_the_order_handed_to_the_coordinator():
    o = order(StoreLine("VASE-1", "Vase", 2, 4000), total=9500, shipping_agorot=2500, discount_agorot=1000,
              note="please wrap", external_id="5551234")
    d = stores.ready(o, resolve(o, {}, CATALOG), oid=stores.order_id("shp", "5551234"), connector_id="shopify",
                     channel="shopify")
    msg = StoreOrderReady(**d)
    assert (msg.order_id, msg.discount_agorot, msg.shipping_agorot, msg.paid_agorot) == ("shp-5551234", 1000, 2500, 9500)
    assert msg.notes == "shopify #1 · please wrap" and msg.paid_at == NOW
    unpaid = stores.ready(order(StoreLine("VASE-1", "Vase", 1, 4000), paid=False), ok := resolve(o, {}, CATALOG),
                          oid="shp-1", connector_id="shopify", channel="shopify")
    assert ok.lines and (unpaid["paid_agorot"], unpaid["paid_at"]) == (0, None)
    assert stores.order_id("shp", "gid://shopify/Order/12") == "shp-gidshopifyOrder12"
    assert len(stores.order_id("shp", "9" * 80)) == 40


def test_our_total_always_equals_what_the_customer_paid():
    from printfarm.contracts.orders import price_order

    wrap = {"W": SkuMap("ignore")}
    # gift wrap (not printed) and tax the store added: extras in shipping, named in the notes
    o = order(StoreLine("VASE-1", "Vase", 2, 4000), StoreLine("W", "Gift wrap", 1, 1000), total=11700,
              shipping_agorot=2500, discount_agorot=1000)
    res = resolve(o, wrap, CATALOG)
    assert res.ignored == ("Gift wrap",)
    msg = StoreOrderReady(**stores.ready(o, res, oid="shp-1", connector_id="shopify", channel="shopify"))
    assert (msg.shipping_agorot, msg.discount_agorot) == (4700, 1000)
    assert msg.notes == "shopify #1 · ₪22.00 store extras in shipping (Gift wrap)"
    assert price_order(msg.items, discount_agorot=msg.discount_agorot, shipping_agorot=msg.shipping_agorot).total == 11700
    # a store total below ours (a discount on lines we left out, rounding): the difference becomes discount
    low = order(StoreLine("VASE-1", "Vase", 1, 4000), total=3500)
    m2 = StoreOrderReady(**stores.ready(low, resolve(low, {}, CATALOG), oid="shp-2", connector_id="s", channel="shopify"))
    assert (m2.discount_agorot, m2.shipping_agorot) == (500, 0)


def test_connector_settings_are_checked():
    s = Shopify()
    assert clean_settings(s.settings, {"shop": " my-shop.myshopify.com ", "only_paid": False}) == {
        "shop": "my-shop.myshopify.com", "only_paid": False}
    for bad, msg in (({"shop": "evil.example.com"}, "not valid"), ({"only_paid": "yes"}, "takes a bool"),
                     ({"nope": 1}, "unknown setting"), ({"fake_batch": 0}, "between"),
                     ({"api_version": "2026-05"}, "not valid")):
        with pytest.raises(ValueError, match=msg):
            clean_settings(s.settings, bad)
    assert s.settings_with_defaults({"shop": "a.myshopify.com", "fail_push": True})["api_version"] == "2026-07"


def test_live_mode_needs_a_shop_and_credentials():
    s = Shopify()
    assert len(s.live_missing({}, {"shop": ""})) == 2
    assert s.live_missing({"SHOPIFY_ACCESS_TOKEN": "t"}, {"shop": "a.myshopify.com"}) == []
    assert s.live_missing({"SHOPIFY_CLIENT_ID": "i", "SHOPIFY_CLIENT_SECRET": "s"}, {"shop": "a.myshopify.com"}) == []
    assert s.live_missing({"SHOPIFY_CLIENT_ID": "i"}, {"shop": "a.myshopify.com"}) == [
        "SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (or SHOPIFY_ACCESS_TOKEN)"]


def ctx(mode="fake", cursor=None, settings=None, secrets=None):
    return SyncContext(mode=mode, cursor=cursor or {}, settings=settings or {}, secrets=secrets or {}, now=NOW,
                       log=lambda m: None)


def test_fake_orders_are_stable_per_batch_and_new_per_batch():
    s = Shopify()
    first = asyncio.run(s.sync(ctx(settings={"fake_batch": 7})))
    again = asyncio.run(s.sync(ctx(cursor=first.cursor, settings={"fake_batch": 7})))
    assert [r.payload_hash for r in first.records] == [r.payload_hash for r in again.records]
    assert [r.internal_id for r in first.records] == ["shp-F7-1", "shp-F7-2", "shp-F7-3"]
    nxt = asyncio.run(s.sync(ctx(cursor=first.cursor, settings={"fake_batch": 8})))
    assert nxt.records[0].external_id == "F8-1"
    o = fake_orders(1, NOW)[1]
    assert o.total_agorot == sum(x.quantity * x.unit_price_agorot for x in o.lines) + o.shipping_agorot - o.discount_agorot


def test_a_fake_push_succeeds_or_fails_on_request():
    s, p = Shopify(), stores.Push("tracking", "F1-1", "shp-F1-1", {"tracking": "RR1"})
    asyncio.run(s.push(ctx(), p))
    with pytest.raises(SyncError, match="refuse"):
        asyncio.run(s.push(ctx(settings={"fail_push": True}), p))


class FakeHttp:
    """Records requests and answers them in order (no network)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def post(self, url, **kw):
        import httpx

        self.calls.append((url, kw))
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def test_live_fetch_uses_client_credentials_and_pages():
    page1 = {"data": {"orders": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [NODE]}}}
    page2 = {"data": {"orders": {"pageInfo": {"hasNextPage": False}, "nodes": [
        {**NODE, "legacyResourceId": "2", "updatedAt": "2026-11-19T11:00:00Z"}]}}}
    http = FakeHttp((200, {"access_token": "tok", "expires_in": 86399}), (200, page1), (200, page2))
    s = Shopify(client=http)
    out = asyncio.run(s.sync(ctx("live", settings={"shop": "a.myshopify.com", "import_since": "2026-11-01"},
                                 secrets={"SHOPIFY_CLIENT_ID": "i", "SHOPIFY_CLIENT_SECRET": "s"})))
    assert [r.external_id for r in out.records] == ["5551234", "2"]
    assert out.cursor == {"updated_since": "2026-11-19T11:00:00Z", "started": "2026-11-01"}
    assert http.calls[0][0] == "https://a.myshopify.com/admin/oauth/access_token"
    assert http.calls[0][1]["data"]["grant_type"] == "client_credentials"
    assert http.calls[1][0] == "https://a.myshopify.com/admin/api/2026-07/graphql.json"
    assert http.calls[1][1]["headers"] == {"X-Shopify-Access-Token": "tok"}
    assert http.calls[1][1]["json"]["variables"]["q"] == "created_at:>='2026-11-01'"
    assert http.calls[2][1]["json"]["variables"]["after"] == "c1"


def test_live_errors_say_what_is_wrong_without_secrets():
    for status, body, msg in ((401, {}, "refused the token"), (429, {}, "slow down"), (500, {}, "HTTP 500"),
                              (200, {"errors": [{"message": "Access denied for orders"}]}, "Access denied")):
        s = Shopify(client=FakeHttp((status, body)))
        with pytest.raises(SyncError, match=msg) as exc:
            asyncio.run(s.sync(ctx("live", settings={"shop": "a.myshopify.com"}, secrets={"SHOPIFY_ACCESS_TOKEN": "sekret"})))
        assert "sekret" not in str(exc.value)


def test_live_push_fulfils_the_open_fulfillment_orders_with_tracking():
    fos = {"data": {"order": {"fulfillmentOrders": {"nodes": [{"id": "gid://FO/1", "status": "OPEN"},
                                                               {"id": "gid://FO/2", "status": "CLOSED"}]}}}}
    done = {"data": {"fulfillmentCreate": {"fulfillment": {"id": "f"}, "userErrors": []}}}
    http = FakeHttp((200, fos), (200, done))
    s = Shopify(client=http)
    asyncio.run(s.push(ctx("live", settings={"shop": "a.myshopify.com", "notify_customer": False},
                           secrets={"SHOPIFY_ACCESS_TOKEN": "t"}),
                       stores.Push("tracking", "5551234", "shp-5551234", {"carrier": "Israel Post", "tracking": "RR1"})))
    assert http.calls[0][1]["json"]["variables"] == {"id": "gid://shopify/Order/5551234"}
    assert http.calls[1][1]["json"]["variables"]["fulfillment"] == {
        "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": "gid://FO/1"}], "notifyCustomer": False,
        "trackingInfo": {"company": "Israel Post", "number": "RR1"}}
    refused = FakeHttp((200, fos), (200, {"data": {"fulfillmentCreate": {"userErrors": [{"message": "no"}]}}}))
    with pytest.raises(SyncError, match="refused the fulfillment: no"):
        asyncio.run(Shopify(client=refused).push(ctx("live", settings={"shop": "a.myshopify.com"},
                                                     secrets={"SHOPIFY_ACCESS_TOKEN": "t"}),
                                                 stores.Push("tracking", "1", "shp-1", {})))
    nothing = FakeHttp((200, {"data": {"order": {"fulfillmentOrders": {"nodes": [{"id": "x", "status": "CLOSED"}]}}}}))
    asyncio.run(Shopify(client=nothing).push(ctx("live", settings={"shop": "a.myshopify.com"},
                                                 secrets={"SHOPIFY_ACCESS_TOKEN": "t"}),
                                             stores.Push("tracking", "1", "shp-1", {})))
    assert len(nothing.calls) == 1                                # fulfilled at Shopify already: nothing sent


def test_map_command_needs_a_sku_for_catalog():
    with pytest.raises(ValueError, match="needs a SKU"):
        MapStoreSku(connector_id="shopify", external_key="X", product="catalog")
    assert MapStoreSku(connector_id="shopify", external_key="X", product="catalog", remove=True).remove


# ---------------------------------------------------------------- gateway


def _gateway():
    import gateway_support  # noqa: F401 - sets the gateway's environment

    from gateway.app import commands, today
    from gateway.app import stores as gw_stores
    return commands, gw_stores, today


def test_the_gateway_checks_connector_settings_and_knows_the_store_commands():
    commands, _, _ = _gateway()
    spec = commands.COMMANDS["integrations.configure"]
    data = {"connector_id": "shopify", "settings": {"shop": "x.myshopify.com"}}
    spec.fill(data, "c")
    assert data["settings"] == {"shop": "x.myshopify.com"}
    for bad in ({"connector_id": "shopify", "settings": {"shop": "x.com"}}, {"connector_id": "nope", "settings": {}},
                {"connector_id": "shopify", "settings": []}):
        with pytest.raises(ValueError):
            spec.fill(bad, "c")
    assert commands.COMMANDS["integrations.map_sku"].min_role == "operator"
    assert commands.COMMANDS["integrations.store_order"].min_role == "operator"


def test_the_map_this_queue_groups_by_sku():
    _, gw, _ = _gateway()
    o1 = fake_orders(3, NOW)[0].to_payload()
    o2 = fake_orders(3, NOW)[2].to_payload()
    rows = [{"connector_id": "shopify", "name": "#3001", "payload": o1,
             "problems": [{"kind": "unmapped", "key": "NAMEPLATE-BLACK", "title": "Personalised nameplate"}]},
            {"connector_id": "shopify", "name": "#3003", "payload": o2,
             "problems": [{"kind": "unmapped", "key": "NAMEPLATE-BLACK", "title": "Personalised nameplate"},
                          {"kind": "problem", "key": "", "title": "#3003", "message": "x"}]}]
    assert gw.queue(rows) == [{"connector_id": "shopify", "external_key": "NAMEPLATE-BLACK",
                               "title": "Personalised nameplate", "orders": ["#3001", "#3003"], "properties": ["Name"],
                               "variants": ["Black"]}]


def test_today_says_when_store_orders_wait():
    _, _, today = _gateway()
    assert today.store_orders({}, NOW) == []
    items = today.store_orders({"waiting": 2, "since": NOW - timedelta(hours=1), "failed_pushes": 1}, NOW)
    assert [(i.id, i.count, i.title) for i in items] == [
        ("store:map", 2, "2 store orders wait for you"), ("store:push", 1, "1 tracking update did not reach the store")]
    assert items[0].action == {"type": "link", "to": "/store", "label": "Store orders"} and items[0].age_s == 3600
