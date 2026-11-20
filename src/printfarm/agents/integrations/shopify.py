"""Shopify (Wave 2 / W2-A): reads the shop's orders through the GraphQL Admin API (outbound polling; nothing on the
internet calls this stack) and, when an order of ours ships, marks it fulfilled at Shopify with its tracking.

Credentials, in integrations.env (never in the database or the browser), one of:
* ``SHOPIFY_CLIENT_ID`` + ``SHOPIFY_CLIENT_SECRET``: an app made in Shopify's Dev Dashboard and installed on the shop
  (the way since 2026). The connector asks Shopify for a short-lived token (client credentials grant) and renews it.
* ``SHOPIFY_ACCESS_TOKEN``: the Admin API token of an older custom app made in the shop's admin.

Scopes the app needs: read_orders, read_merchant_managed_fulfillment_orders, write_merchant_managed_fulfillment_orders
(read_customers for the customer's name and e-mail).

Fake mode (the default) never calls Shopify: a few deterministic test orders per "test batch" (settings.fake_batch),
so the dashboard, the tests and CI see the whole flow (map this, import, tracking back) without a shop.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

import httpx

from printfarm.agents.integrations.framework import SecretSpec, SettingSpec, SyncContext, SyncError
from printfarm.agents.integrations.stores import Push, StoreConnector, StoreLine, StoreOrder, agorot

SHOP_PATTERN = r"[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com"
PAGE = 50
MAX_PAGES = 10                   # per sync; the cursor continues next time
TIMEOUT_S = 30

ORDERS_QUERY = """
query Orders($first: Int!, $after: String, $q: String) {
  orders(first: $first, after: $after, query: $q, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id legacyResourceId name createdAt updatedAt cancelledAt displayFinancialStatus note email phone
      customer { firstName lastName email phone }
      shippingAddress { name phone address1 address2 city zip }
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      totalDiscountsSet { shopMoney { amount } }
      totalShippingPriceSet { shopMoney { amount } }
      lineItems(first: 50) {
        nodes { sku title variantTitle quantity customAttributes { key value }
                originalUnitPriceSet { shopMoney { amount } } }
      }
    }
  }
}
"""

FULFILLMENT_ORDERS_QUERY = """
query FulfillmentOrders($id: ID!) {
  order(id: $id) { fulfillmentOrders(first: 10) { nodes { id status } } }
}
"""

FULFILL_MUTATION = """
mutation Fulfill($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) { fulfillment { id status } userErrors { field message } }
}
"""

PAID = {"PAID", "PARTIALLY_REFUNDED", "REFUNDED", "PARTIALLY_PAID"}


def parse_order(node: Mapping) -> StoreOrder:
    """One order node of ORDERS_QUERY as a StoreOrder (pure; tested with a recorded answer)."""
    money = lambda key: agorot(((node.get(key) or {}).get("shopMoney") or {}).get("amount"))   # noqa: E731
    customer = node.get("customer") or {}
    ship = node.get("shippingAddress") or {}
    name = " ".join(x for x in (customer.get("firstName"), customer.get("lastName")) if x) or ship.get("name") or ""
    lines = []
    for li in ((node.get("lineItems") or {}).get("nodes") or []):
        props = {str(a.get("key")): str(a.get("value") or "") for a in li.get("customAttributes") or []
                 if a.get("key") and not str(a.get("key")).startswith("_")}       # "_..." are the theme's own
        lines.append(StoreLine(sku=li.get("sku") or "", title=li.get("title") or "", variant=li.get("variantTitle") or "",
                               quantity=int(li.get("quantity") or 0),
                               unit_price_agorot=agorot(((li.get("originalUnitPriceSet") or {}).get("shopMoney") or {})
                                                        .get("amount")), properties=props))
    created = node.get("createdAt")
    status = str(node.get("displayFinancialStatus") or "")
    return StoreOrder(
        external_id=str(node.get("legacyResourceId") or str(node.get("id", "")).rsplit("/", 1)[-1]),
        name=str(node.get("name") or ""), customer_name=name,
        customer_ref=customer.get("email") or node.get("email") or customer.get("phone") or node.get("phone") or "",
        placed_at=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        paid=status in PAID, financial_status=status.lower(), cancelled=bool(node.get("cancelledAt")),
        lines=tuple(lines), total_agorot=money("currentTotalPriceSet"), shipping_agorot=money("totalShippingPriceSet"),
        discount_agorot=money("totalDiscountsSet"),
        currency=(((node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}).get("currencyCode") or "ILS"),
        note=str(node.get("note") or "")[:200], updated_at=str(node.get("updatedAt") or ""),
        ship_to={"name": str(ship.get("name") or name)[:80],
                 "street": " ".join(x for x in (ship.get("address1"), ship.get("address2")) if x)[:120],
                 "city": str(ship.get("city") or "")[:60], "zip": str(ship.get("zip") or "")[:12],
                 "phone": str(ship.get("phone") or customer.get("phone") or "")[:30]} if ship else {})


def search_query(updated_since: str | None, import_since: str | None) -> str:
    """Shopify's search syntax: orders changed since the cursor (or placed since the owner's start date)."""
    if updated_since:
        return f"updated_at:>'{updated_since}'"
    if import_since:
        return f"created_at:>='{import_since}'"
    return ""


def fake_orders(batch: int, base: datetime) -> list[StoreOrder]:
    """Three test orders per batch: a nameplate, two nameplates with gift wrap, shipping and a discount, and one
    not paid yet. Their SKUs (NAMEPLATE-BLACK, NAMEPLATE-WHITE, GIFT-WRAP) are not ours: they show "map this"."""
    def nameplate(color: str, name: str, qty: int = 1) -> StoreLine:
        return StoreLine(sku=f"NAMEPLATE-{color.upper()}", title="Personalised nameplate", variant=color.title(),
                         quantity=qty, unit_price_agorot=4900, properties={"Name": name})
    at = base.replace(microsecond=0)
    return [
        StoreOrder(f"F{batch}-1", f"#{batch}001", "Noa Levi", "noa@example.com", at - timedelta(hours=3), True, "paid",
                   False, (nameplate("black", "Noa"),), 4900,
                   ship_to={"name": "Noa Levi", "street": "Herzl 1", "city": "Tel Aviv", "zip": "6100001",
                            "phone": "050-0000001"}),
        StoreOrder(f"F{batch}-2", f"#{batch}002", "Dan Cohen", "dan@example.com", at - timedelta(hours=2), True, "paid",
                   False, (nameplate("white", "Dan", 2), StoreLine("GIFT-WRAP", "Gift wrap", 1, 1000)),
                   4900 * 2 + 1000 + 2500 - 1000, shipping_agorot=2500, discount_agorot=1000, note="Birthday gift"),
        StoreOrder(f"F{batch}-3", f"#{batch}003", "Gal Ben-David", "gal@example.com", at - timedelta(hours=1), False,
                   "pending", False, (nameplate("black", "Gal"),), 4900),
    ]


class Shopify(StoreConnector):
    id = "shopify"
    label = "Shopify"
    channel = "shopify"
    order_prefix = "shp"
    default_interval_s = 300
    secrets = (
        SecretSpec("SHOPIFY_CLIENT_ID", "Client ID of your Dev Dashboard app", required=False),
        SecretSpec("SHOPIFY_CLIENT_SECRET", "Client secret of your Dev Dashboard app", required=False),
        SecretSpec("SHOPIFY_ACCESS_TOKEN", "Admin API token of an older custom app (instead of the two above)",
                   required=False),
    )
    settings = (
        SettingSpec("shop", "Shop address (your-shop.myshopify.com)", "", pattern=SHOP_PATTERN),
        SettingSpec("api_version", "Admin API version", "2026-07", pattern=r"20\d\d-(01|04|07|10)"),
        SettingSpec("import_since", "Import orders placed from (YYYY-MM-DD; empty: from when it was switched on)", "",
                    pattern=r"20\d\d-\d\d-\d\d"),
        SettingSpec("only_paid", "Import only paid orders", True),
        SettingSpec("notify_customer", "Shopify e-mails the customer the tracking", True),
        SettingSpec("fake_batch", "Test orders batch (fake mode)", 1, minimum=1, maximum=10_000_000),
    )

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._token: tuple[str, float] | None = None          # (token, expires at, monotonic)

    def live_missing(self, values: Mapping[str, str], settings: Mapping) -> list[str]:
        missing = [] if settings.get("shop") else ["the shop address (settings)"]
        if not values.get("SHOPIFY_ACCESS_TOKEN") and not (values.get("SHOPIFY_CLIENT_ID")
                                                           and values.get("SHOPIFY_CLIENT_SECRET")):
            missing.append("SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (or SHOPIFY_ACCESS_TOKEN)")
        return missing

    # ------------------------------------------------------------ fake

    async def fetch(self, ctx: SyncContext) -> tuple[Sequence[StoreOrder], dict]:
        settings = self.settings_with_defaults(ctx.settings)
        if ctx.mode != "live":
            batch = int(settings["fake_batch"])
            base = datetime.fromisoformat(ctx.cursor["base"]) if ctx.cursor.get("batch") == batch else ctx.now
            return fake_orders(batch, base), {"batch": batch, "base": base.isoformat()}
        return await self._fetch_live(ctx, settings)

    async def push(self, ctx: SyncContext, push: Push) -> None:
        if ctx.mode != "live":
            if ctx.settings.get("fail_push"):
                raise SyncError("the test shop is set to refuse tracking (settings.fail_push)")
            ctx.log(f"fake: order {push.external_id} fulfilled with {push.payload.get('tracking') or 'no tracking'}")
            return
        settings = self.settings_with_defaults(ctx.settings)
        gid = f"gid://shopify/Order/{push.external_id}"
        data = await self._graphql(ctx, settings, FULFILLMENT_ORDERS_QUERY, {"id": gid})
        open_fos = [n["id"] for n in (((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes") or [])
                    if n.get("status") in ("OPEN", "IN_PROGRESS", "SCHEDULED")]
        if not open_fos:
            ctx.log(f"order {push.external_id} has nothing left to fulfil at Shopify")
            return                                            # fulfilled there already: nothing to send
        fulfillment: dict = {"lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": f} for f in open_fos],
                             "notifyCustomer": bool(settings["notify_customer"])}
        tracking = {k: v for k, v in (("company", push.payload.get("carrier")), ("number", push.payload.get("tracking")),
                                      ("url", push.payload.get("url"))) if v}
        if tracking:
            fulfillment["trackingInfo"] = tracking
        out = (await self._graphql(ctx, settings, FULFILL_MUTATION, {"fulfillment": fulfillment})).get("fulfillmentCreate") or {}
        if out.get("userErrors"):
            raise SyncError("Shopify refused the fulfillment: " + "; ".join(e.get("message", "") for e in out["userErrors"])[:300])

    # ------------------------------------------------------------ live

    async def _fetch_live(self, ctx: SyncContext, settings: Mapping) -> tuple[list[StoreOrder], dict]:
        since = ctx.cursor.get("updated_since")
        import_since = settings["import_since"] or ctx.cursor.get("started") or ctx.now.date().isoformat()
        q, after, orders, newest = search_query(since, import_since), None, [], since
        for _ in range(MAX_PAGES):
            data = await self._graphql(ctx, settings, ORDERS_QUERY, {"first": PAGE, "after": after, "q": q or None})
            page = data.get("orders") or {}
            for node in page.get("nodes") or []:
                orders.append(parse_order(node))
                if node.get("updatedAt") and (newest is None or node["updatedAt"] > newest):
                    newest = node["updatedAt"]
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
        return orders, {"updated_since": newest, "started": import_since}

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT_S)
        return self._client

    async def _access_token(self, ctx: SyncContext, shop: str) -> str:
        if ctx.secrets.get("SHOPIFY_ACCESS_TOKEN"):
            return ctx.secrets["SHOPIFY_ACCESS_TOKEN"]
        if self._token and self._token[1] > time.monotonic() + 60:
            return self._token[0]
        r = await (await self._http()).post(f"https://{shop}/admin/oauth/access_token", data={
            "grant_type": "client_credentials", "client_id": ctx.secrets.get("SHOPIFY_CLIENT_ID", ""),
            "client_secret": ctx.secrets.get("SHOPIFY_CLIENT_SECRET", "")})
        if r.status_code != 200:
            raise SyncError(f"Shopify did not give a token (HTTP {r.status_code}): is the app installed on {shop}?")
        body = r.json()
        self._token = (body["access_token"], time.monotonic() + float(body.get("expires_in") or 3600))
        return self._token[0]

    async def _graphql(self, ctx: SyncContext, settings: Mapping, query: str, variables: dict) -> dict:
        shop = str(settings.get("shop") or "")
        token = await self._access_token(ctx, shop)
        r = await (await self._http()).post(f"https://{shop}/admin/api/{settings['api_version']}/graphql.json",
                                            json={"query": query, "variables": variables},
                                            headers={"X-Shopify-Access-Token": token})
        if r.status_code == 401:
            self._token = None
            raise SyncError("Shopify refused the token (401): check the app's credentials and scopes")
        if r.status_code == 429:
            raise SyncError("Shopify asked to slow down (429): retrying later")
        if r.status_code != 200:
            raise SyncError(f"Shopify answered HTTP {r.status_code}")
        body = r.json()
        if body.get("errors"):
            msg = "; ".join(str(e.get("message", e)) for e in body["errors"])[:300]
            raise SyncError(f"Shopify: {msg}")
        return body.get("data") or {}
