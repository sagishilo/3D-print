"""Migration 019 and the store flow on a real Postgres: Shopify (fake) synced, its orders waiting for a mapping (and
the not-paid one for payment), an owner's mapping importing them once (StoreOrderReady in the outbox), the coordinator
creating the order and its payment once, a skipped order, connector settings, and the tracking pushed back when the
order ships (retried, then a person told). Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")


@pytest.fixture
async def agent():
    from printfarm.agents.integrations.agent import IntegrationsAgent
    from printfarm.config import Settings
    from printfarm.store.db import Database

    db = Database(DB_URL)
    await db.open()
    await db.migrate()
    async with db.tx() as conn:
        for t in ("integrations.pushes", "integrations.store_orders", "integrations.sku_map", "integrations.runs",
                  "integrations.mappings", "integrations.sync_state", "integrations.connectors", "notify.outbox"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.execute("DELETE FROM messaging.outbox WHERE producer IN ('integrations', 'coordinator')")
        for t in ("coordinator.payments", "coordinator.saga_history", "coordinator.sagas", "coordinator.order_items",
                  "coordinator.orders"):
            await conn.execute(f"DELETE FROM {t} WHERE {'saga_id' if 'saga' in t else 'order_id'} LIKE 'shp-%%'")
    a = IntegrationsAgent(None, db, Settings(database_url=DB_URL), None)
    a.spawn = lambda coro: coro.close()
    await a.on_start()
    async with db.tx() as conn:
        await conn.execute("UPDATE integrations.connectors SET enabled = true WHERE connector_id = 'shopify'")
    yield a, db
    await db.close()


async def rows(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def send(db, handler, payload):
    from printfarm.contracts import new_message

    env = new_message(payload, source="/test")
    async with db.tx() as conn:
        await handler(env, payload, conn)
    return env


async def statuses(db):
    return {r["external_id"]: r["status"] for r in await rows(
        db, "SELECT external_id, status FROM integrations.store_orders WHERE connector_id = 'shopify'")}


async def ready(db):
    return [r["envelope"]["data"] for r in await rows(
        db, "SELECT envelope FROM messaging.outbox WHERE subject = 'evt.integrations.store_order.ready' ORDER BY seq")]


async def test_store_orders_wait_for_a_mapping_then_import_once(agent):
    from printfarm.contracts.messages import MapStoreSku, StoreOrderAction

    a, db = agent
    c = await rows(db, "SELECT enabled, mode, settings FROM integrations.connectors WHERE connector_id = 'shopify'")
    assert c == [{"enabled": True, "mode": "fake", "settings": {}}]
    r = await a.run("shopify", trigger="manual")
    assert (r.ok, r.created) == (True, 3)
    assert await statuses(db) == {"F1-1": "needs_mapping", "F1-2": "needs_mapping", "F1-3": "waiting_payment"}
    notes = await rows(db, "SELECT dedupe_key FROM notify.outbox WHERE topic = 'store' ORDER BY id")
    assert [n["dedupe_key"] for n in notes] == ["store_map:shopify:F1-1", "store_map:shopify:F1-2"]

    for key, color in (("NAMEPLATE-BLACK", "black"), ("NAMEPLATE-WHITE", "white")):
        await send(db, a.map_sku, MapStoreSku(connector_id="shopify", external_key=key, product="nameplate", color=color,
                                              mapped_by="dana"))
    assert (await statuses(db))["F1-1"] == "imported"
    assert (await statuses(db))["F1-2"] == "needs_mapping"          # its gift wrap is still unknown
    await send(db, a.map_sku, MapStoreSku(connector_id="shopify", external_key="GIFT-WRAP", product="ignore",
                                          mapped_by="dana"))
    assert await statuses(db) == {"F1-1": "imported", "F1-2": "imported", "F1-3": "waiting_payment"}
    out = await ready(db)
    assert [d["order_id"] for d in out] == ["shp-F1-1", "shp-F1-2"]
    two = out[1]
    assert [(i["product"], i["text"], i["color"], i["quantity"]) for i in two["items"]] == [("nameplate", "Dan", "white", 2)]
    # the gift wrap is not printed, but the customer paid for it: our total is what they paid
    assert (two["shipping_agorot"], two["discount_agorot"], two["paid_agorot"], two["channel"]) == (3500, 1000, 12300, "shopify")
    assert "₪10.00 store extras in shipping (Gift wrap)" in two["notes"]

    # a repeated sync, or a mapping changed later, never imports twice
    await a.run("shopify", trigger="manual")
    await send(db, a.map_sku, MapStoreSku(connector_id="shopify", external_key="GIFT-WRAP", product="ignore",
                                          mapped_by="dana"))
    assert len(await ready(db)) == 2
    # skip the unpaid one (handled by hand), then take it back
    await send(db, a.store_order_action, StoreOrderAction(connector_id="shopify", external_id="F1-3", action="skip"))
    assert (await statuses(db))["F1-3"] == "skipped"
    await a.run("shopify", trigger="manual")
    assert (await statuses(db))["F1-3"] == "skipped"
    await send(db, a.store_order_action, StoreOrderAction(connector_id="shopify", external_id="F1-3", action="unskip"))
    assert (await statuses(db))["F1-3"] == "waiting_payment"


async def test_unpaid_orders_import_when_the_owner_allows_it(agent):
    from printfarm.contracts.messages import ConfigureConnector, MapStoreSku

    a, db = agent
    await send(db, a.configure, ConfigureConnector(connector_id="shopify", settings={"only_paid": False, "fake_batch": 2},
                                                   changed_by="owner"))
    await send(db, a.configure, ConfigureConnector(connector_id="shopify", settings={"shop": "evil.com"}, changed_by="o"))
    c = await rows(db, "SELECT settings FROM integrations.connectors WHERE connector_id = 'shopify'")
    assert c[0]["settings"] == {"only_paid": False, "fake_batch": 2}   # the bad change was refused whole
    await send(db, a.map_sku, MapStoreSku(connector_id="shopify", external_key="NAMEPLATE-BLACK", product="nameplate"))
    await a.run("shopify", trigger="manual")
    assert await statuses(db) == {"F2-1": "imported", "F2-2": "needs_mapping", "F2-3": "imported"}
    assert {d["order_id"]: d["paid_agorot"] for d in await ready(db)} == {"shp-F2-1": 4900, "shp-F2-3": 0}


async def test_the_coordinator_creates_the_order_and_its_payment_once(agent):
    from printfarm.agents.coordinator.agent import CoordinatorAgent
    from printfarm.config import Settings
    from printfarm.contracts.messages import MapStoreSku, StoreOrderReady

    a, db = agent
    await send(db, a.map_sku, MapStoreSku(connector_id="shopify", external_key="NAMEPLATE-BLACK", product="nameplate"))
    await a.run("shopify", trigger="manual")
    data = (await ready(db))[0]
    coord = CoordinatorAgent(None, db, Settings(database_url=DB_URL), None)
    msg = StoreOrderReady(**data)
    await send(db, coord.store_order_ready, msg)
    await send(db, coord.store_order_ready, msg)                     # redelivered: nothing twice
    o = await rows(db, "SELECT channel, customer_name, total_agorot, created_by FROM coordinator.orders WHERE order_id = 'shp-F1-1'")
    assert o == [{"channel": "shopify", "customer_name": "Noa Levi", "total_agorot": 4900, "created_by": "shopify"}]
    p = await rows(db, "SELECT payment_id, amount_agorot, method, receipt_no FROM coordinator.payments WHERE order_id = 'shp-F1-1'")
    assert p == [{"payment_id": "pay-shp-F1-1", "amount_agorot": 4900, "method": "card", "receipt_no": "#1001"}]
    s = await rows(db, "SELECT state FROM coordinator.sagas WHERE saga_id = 'shp-F1-1'")
    assert s and s[0]["state"] != "LEAD"


async def test_tracking_goes_back_to_the_store_and_failures_retry_then_tell(agent):
    from psycopg.types.json import Jsonb

    from printfarm.contracts.messages import MapStoreSku, OrderShipped

    a, db = agent
    for key in ("NAMEPLATE-BLACK", "NAMEPLATE-WHITE"):
        await send(db, a.map_sku, MapStoreSku(connector_id="shopify", external_key=key, product="nameplate"))
    await send(db, a.map_sku, MapStoreSku(connector_id="shopify", external_key="GIFT-WRAP", product="ignore"))
    await a.run("shopify", trigger="manual")
    await send(db, a.order_shipped, OrderShipped(order_id="shp-F1-1", total_agorot=4900, carrier="Israel Post",
                                                tracking="RR123IL"))
    await send(db, a.order_shipped, OrderShipped(order_id="shp-F1-1", total_agorot=4900))     # once only
    await send(db, a.order_shipped, OrderShipped(order_id="ord-manual", total_agorot=100))   # not a store order
    now = datetime.now(UTC)
    assert await a.push_due(now) == 1
    p = await rows(db, "SELECT order_id, status, attempts, payload FROM integrations.pushes")
    assert p == [{"order_id": "shp-F1-1", "status": "sent", "attempts": 1,
                  "payload": {"carrier": "Israel Post", "tracking": "RR123IL", "url": ""}}]

    async with db.tx() as conn:
        await conn.execute("UPDATE integrations.connectors SET settings = %s WHERE connector_id = 'shopify'",
                           (Jsonb({"fail_push": True}),))
    await send(db, a.order_shipped, OrderShipped(order_id="shp-F1-2", total_agorot=12300))
    for i in range(8):
        await a.push_due(now + timedelta(days=i + 1))
    failed = await rows(db, "SELECT status, attempts, error FROM integrations.pushes WHERE order_id = 'shp-F1-2'")
    assert failed[0]["status"] == "failed" and failed[0]["attempts"] == 8 and "refuse" in failed[0]["error"]
    notes = await rows(db, "SELECT min_role FROM notify.outbox WHERE dedupe_key LIKE 'push:%%'")
    assert notes == [{"min_role": "owner"}]
