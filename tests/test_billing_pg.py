"""Migration 020 and the integrations agent's documents and parcels on a real Postgres: a payment gets its tax
invoice-receipt from Morning (test mode) once, a refund its credit invoice linked to it, an osek patur's refund is
"by hand"; failures retry then tell an owner; a person can issue a missing document. An order that passed QC gets an
Israel Post parcel (test label, then in transit, then delivered by the courier's tracking), a hand-typed carrier, or
a refusal with the reason; the coordinator ships it. Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")

O1, O2 = "pgbill-1", "pgbill-2"


@pytest.fixture
async def agent():
    from printfarm.agents.integrations.agent import IntegrationsAgent
    from printfarm.config import Settings
    from printfarm.store.db import Database

    db = Database(DB_URL)
    await db.open()
    await db.migrate()
    async with db.tx() as conn:
        for t in ("integrations.documents", "integrations.shipments", "integrations.pushes", "integrations.store_orders",
                  "integrations.sku_map", "integrations.runs", "integrations.mappings", "integrations.sync_state",
                  "integrations.connectors", "notify.outbox"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.execute("DELETE FROM messaging.outbox WHERE producer = 'integrations'")
        for oid in (O1, O2):
            await conn.execute("DELETE FROM coordinator.payments WHERE order_id = %s", (oid,))
            await conn.execute("DELETE FROM coordinator.sagas WHERE saga_id = %s", (oid,))
            await conn.execute("DELETE FROM coordinator.order_items WHERE order_id = %s", (oid,))
            await conn.execute("DELETE FROM coordinator.orders WHERE order_id = %s", (oid,))
            await conn.execute(
                "INSERT INTO coordinator.orders (order_id, customer_name, customer_ref, channel, subtotal_agorot, "
                "discount_agorot, shipping_agorot, total_agorot, units, created_by) "
                "VALUES (%s, 'Noa Levi', 'noa@example.com', 'manual', 9800, 0, 2500, 12300, 2, 't')", (oid,))
            await conn.execute(
                "INSERT INTO coordinator.order_items (order_id, item_id, product, sku, text, material, color, quantity, "
                "unit_price_agorot, line_total_agorot, niche) VALUES (%s, '1', 'nameplate', '', 'Dan', 'PLA', 'black', 2, "
                "4900, 9800, 'unassigned')", (oid,))
            await conn.execute(
                "INSERT INTO coordinator.sagas (saga_id, saga_type, state, context) VALUES (%s, 'order', 'QC', %s)",
                (oid, '{"qc": {"status": "passed"}}' if oid == O1 else '{"qc": {"status": "pending"}}'))
        await conn.execute("UPDATE finance.settings SET business_type = 'murshe' WHERE id = 1")
    a = IntegrationsAgent(None, db, Settings(database_url=DB_URL), None)
    a.spawn = lambda coro: coro.close()
    await a.on_start()
    yield a, db
    async with db.tx() as conn:
        await conn.execute("UPDATE finance.settings SET business_type = 'murshe' WHERE id = 1")
    await db.close()


async def rows(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def send(db, handler, payload):
    from printfarm.contracts import new_message

    env = new_message(payload, source="/test")
    async with db.tx() as conn:
        await handler(env, payload, conn)


async def enable(db, cid, mode="fake", settings="{}"):
    async with db.tx() as conn:
        await conn.execute("UPDATE integrations.connectors SET enabled = true, mode = %s, settings = %s::jsonb "
                           "WHERE connector_id = %s", (mode, settings, cid))


async def paid(db, pid, amount=12300, kind="payment", order=O1):
    async with db.tx() as conn:
        await conn.execute("INSERT INTO coordinator.payments (payment_id, order_id, kind, amount_agorot, method, at, "
                           "recorded_by) VALUES (%s, %s, %s, %s, 'bit', now(), 't')", (pid, order, kind, amount))


async def outbox(db, subject):
    return [r["envelope"]["data"] for r in await rows(
        db, "SELECT envelope FROM messaging.outbox WHERE subject = %s ORDER BY seq", subject)]


async def test_payments_get_their_documents_once_and_refunds_their_credit(agent):
    from printfarm.contracts.messages import PaymentRecorded, PaymentRefunded

    a, db = agent
    await paid(db, "pg-pay-1")
    msg = PaymentRecorded(payment_id="pg-pay-1", order_id=O1, amount_agorot=12300, method="bit", paid_at=datetime.now(UTC))
    await send(db, a.payment_recorded, msg)
    assert await rows(db, "SELECT 1 FROM integrations.documents") == []          # no provider switched on: nothing
    await enable(db, "morning")
    await send(db, a.payment_recorded, msg)
    await send(db, a.payment_recorded, msg)                                      # redelivered: one document
    assert await a.issue_due(datetime.now(UTC)) == 1
    d = await rows(db, "SELECT id, kind, status, number, url, external_id, request FROM integrations.documents")
    assert len(d) == 1 and (d[0]["kind"], d[0]["status"]) == ("invoice_receipt", "issued")
    assert (d[0]["number"], d[0]["url"]) == (str(50000 + d[0]["id"]), f"/api/v1/documents/{d[0]['id']}/preview")
    assert [(x["description"], x["quantity"], x["unit_price_agorot"]) for x in d[0]["request"]["lines"]] == [
        ("Nameplate “Dan”", 2, 4900), ("Shipping", 1, 2500)]
    issued = await outbox(db, "evt.integrations.document.issued")
    assert [(e["status"], e["doc_kind"]) for e in issued] == [("issued", "invoice_receipt")]

    await paid(db, "pg-ref-1", 2500, "refund")
    await send(db, a.payment_refunded, PaymentRefunded(payment_id="pg-ref-1", order_id=O1, amount_agorot=2500,
                                                       method="bit", refunded_at=datetime.now(UTC)))
    await a.issue_due(datetime.now(UTC))
    credit = await rows(db, "SELECT kind, status, request FROM integrations.documents WHERE payment_id = 'pg-ref-1'")
    assert (credit[0]["kind"], credit[0]["status"]) == ("credit", "issued")
    assert credit[0]["request"]["linked_external_id"] == d[0]["external_id"]
    assert credit[0]["request"]["lines"] == [{"description": "Order pgbill-1", "quantity": 1, "unit_price_agorot": 2500}]


async def test_an_osek_patur_refund_is_by_hand_and_receipts_for_payments(agent):
    from printfarm.contracts.messages import PaymentRecorded, PaymentRefunded

    a, db = agent
    await enable(db, "morning")
    async with db.tx() as conn:
        await conn.execute("UPDATE finance.settings SET business_type = 'patur' WHERE id = 1")
    await paid(db, "pg-pay-2", 5000)
    await send(db, a.payment_recorded, PaymentRecorded(payment_id="pg-pay-2", order_id=O1, amount_agorot=5000,
                                                       method="cash", paid_at=datetime.now(UTC)))
    await paid(db, "pg-ref-2", 5000, "refund")
    await send(db, a.payment_refunded, PaymentRefunded(payment_id="pg-ref-2", order_id=O1, amount_agorot=5000,
                                                       method="cash", refunded_at=datetime.now(UTC)))
    await a.issue_due(datetime.now(UTC))
    got = {r["payment_id"]: (r["kind"], r["status"]) for r in await rows(
        db, "SELECT payment_id, kind, status FROM integrations.documents")}
    assert got == {"pg-pay-2": ("receipt", "issued"), "pg-ref-2": ("credit", "manual")}


async def test_failures_retry_then_tell_an_owner_and_a_person_can_issue_again(agent):
    from printfarm.contracts.messages import IssueDocument

    a, db = agent
    await enable(db, "morning", settings='{"fail": true, "auto_issue": false}')
    await paid(db, "pg-pay-3")
    await send(db, a.issue_document, IssueDocument(payment_id="pg-pay-3", requested_by="dana"))   # auto off: by hand
    now = datetime.now(UTC)
    for i in range(8):
        await a.issue_due(now + timedelta(days=i))
    d = await rows(db, "SELECT status, attempts, requested_by FROM integrations.documents")
    assert d == [{"status": "failed", "attempts": 8, "requested_by": "dana"}]
    assert await rows(db, "SELECT min_role FROM notify.outbox WHERE dedupe_key LIKE 'doc:%%'") == [{"min_role": "owner"}]
    await enable(db, "morning")
    await send(db, a.issue_document, IssueDocument(payment_id="pg-pay-3", requested_by="dana"))
    assert await a.issue_due(datetime.now(UTC)) == 1
    assert (await rows(db, "SELECT status FROM integrations.documents"))[0]["status"] == "issued"


async def test_parcels_with_israel_post_by_hand_or_refused(agent):
    from printfarm.contracts.messages import CreateShipment, ShipTo, UpdateShipment

    a, db = agent
    to = ShipTo(name="Noa Levi", street="Herzl 1", city="Tel Aviv", zip="6100001")

    async def ship(**kw):
        await send(db, a.create_shipment, CreateShipment(created_by="dana", **kw))

    await ship(order_id=O1, carrier="israel-post", ship_to=to)
    await ship(order_id=O2, carrier="Friend's van")                              # QC not passed yet
    await ship(order_id="pg-nope", carrier="Friend's van")
    refused = [e["reason"] for e in await outbox(db, "evt.integrations.shipment.refused")]
    assert refused == ["Israel Post is switched off (Integrations)", "only an order that passed QC can be shipped",
                       "no order pg-nope"]
    await enable(db, "israel-post", settings='{"sender_name": "PrintFarm"}')
    await ship(order_id=O1, carrier="israel-post", ship_to=to, weight_g=120)
    await ship(order_id=O1, carrier="israel-post", ship_to=to)                   # one live parcel per order
    ready = await outbox(db, "evt.integrations.shipment.ready")
    assert len(ready) == 1 and ready[0]["mark_shipped"] is True and ready[0]["carrier"] == "Israel Post"
    assert ready[0]["tracking"].startswith("RR") and ready[0]["tracking_url"].endswith(ready[0]["tracking"])
    assert ready[0]["label_url"] == "/api/v1/shipments/pcl-pgbill-1-1/label"
    assert "already has parcel pcl-pgbill-1-1" in (await outbox(db, "evt.integrations.shipment.refused"))[-1]["reason"]

    # the courier's tracking moves it on: in transit, then delivered; never back
    now = datetime.now(UTC)
    assert await a.track_due(now) == 1
    assert await a.track_due(now) == 0                                            # checked already this interval
    assert await a.track_due(now + timedelta(hours=1)) == 1
    s = await rows(db, "SELECT status, events FROM integrations.shipments WHERE shipment_id = 'pcl-pgbill-1-1'")
    assert s[0]["status"] == "delivered" and [e["status"] for e in s[0]["events"]] == ["label_created", "in_transit", "delivered"]
    await send(db, a.update_shipment, UpdateShipment(shipment_id="pcl-pgbill-1-1", status="cancelled", changed_by="d"))
    still = await rows(db, "SELECT status FROM integrations.shipments WHERE shipment_id = 'pcl-pgbill-1-1'")
    assert still[0]["status"] == "delivered"


async def test_live_israel_post_needs_a_typed_tracking_number(agent):
    from printfarm.contracts.messages import CreateShipment, UpdateShipment

    a, db = agent
    await enable(db, "israel-post", mode="live")
    for tracking in ("", "hello"):
        await send(db, a.create_shipment, CreateShipment(order_id=O1, carrier="israel-post", tracking=tracking, created_by="d"))
    assert [e["reason"] for e in await outbox(db, "evt.integrations.shipment.refused")] == [
        "make the label at Israel Post and type its tracking number", "hello is not a Israel Post tracking number"]
    await send(db, a.create_shipment, CreateShipment(order_id=O1, carrier="israel-post", tracking="rr123456789il",
                                                     created_by="d"))
    r = (await outbox(db, "evt.integrations.shipment.ready"))[0]
    assert (r["tracking"], r["tracking_url"]) == ("RR123456789IL", "https://israelpost.co.il/itemtrace?itemcode=RR123456789IL")
    assert await a.track_due(datetime.now(UTC)) == 0                              # no public tracking: a person moves it
    await send(db, a.update_shipment, UpdateShipment(shipment_id="pcl-pgbill-1-1", status="in_transit", changed_by="dana"))
    await send(db, a.update_shipment, UpdateShipment(shipment_id="pcl-pgbill-1-1", status="delivered", changed_by="dana"))
    moved = await outbox(db, "evt.integrations.shipment.status")
    assert [(m["previous"], m["status"], m["by"]) for m in moved] == [("label_created", "in_transit", "dana"),
                                                                     ("in_transit", "delivered", "dana")]


async def test_a_hand_typed_carrier_and_a_cancelled_label(agent):
    from printfarm.contracts.messages import CreateShipment, UpdateShipment

    a, db = agent
    await send(db, a.create_shipment, CreateShipment(order_id=O1, carrier="Friend's van", tracking="T-1",
                                                     tracking_url="https://van.example/T-1", created_by="d"))
    await send(db, a.update_shipment, UpdateShipment(shipment_id="pcl-pgbill-1-1", status="cancelled", changed_by="d"))
    await send(db, a.create_shipment, CreateShipment(order_id=O1, carrier="Pickup", created_by="d"))
    s = await rows(db, "SELECT shipment_id, carrier, status, connector_id FROM integrations.shipments ORDER BY created_at")
    assert [(x["shipment_id"], x["carrier"], x["status"], x["connector_id"]) for x in s] == [
        ("pcl-pgbill-1-1", "Friend's van", "cancelled", None), ("pcl-pgbill-1-2", "Pickup", "label_created", None)]


async def test_the_coordinator_ships_on_a_ready_parcel(agent):
    from printfarm.agents.coordinator.agent import CoordinatorAgent
    from printfarm.config import Settings
    from printfarm.contracts.messages import ShipmentReady

    _, db = agent
    coord = CoordinatorAgent(None, db, Settings(database_url=DB_URL), None)
    seen = []

    async def advance(env, msg, conn):
        seen.append(msg)

    coord._advance = advance
    await send(db, coord.shipment_ready, ShipmentReady(shipment_id="s", order_id=O1, carrier="Israel Post",
                                                       tracking="RR1IL", tracking_url="https://t", by="dana"))
    await send(db, coord.shipment_ready, ShipmentReady(shipment_id="s", order_id=O1, carrier="x", mark_shipped=False))
    assert [(m.order_id, m.carrier, m.tracking, m.tracking_url, m.shipped_by) for m in seen] == [
        (O1, "Israel Post", "RR1IL", "https://t", "dana")]
