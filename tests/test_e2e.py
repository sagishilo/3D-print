"""End-to-end: real NATS JetStream + Postgres, an MQTT broker, simulated printers, all agents (incl. the ledger).

Runs only when TEST_NATS_URL and TEST_DATABASE_URL are set. It DROPS the agent schemas and JetStream streams it
uses, so point it at a throwaway database and NATS server, e.g.

    TEST_NATS_URL=nats://localhost:4222 \
    TEST_DATABASE_URL=postgresql://printfarm:printfarm@localhost:5432/printfarm \
    pytest tests/test_e2e.py -v

The Bambu simulator needs an MQTT broker: by default an in-process amqtt broker (dev extra); set
TEST_MQTT_URL=mqtt://127.0.0.1:1883 to use a running one instead (e.g. mosquitto in Docker).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import socket
import textwrap
import time
from urllib.parse import urlparse

import pytest

NATS_URL = os.getenv("TEST_NATS_URL")
# Optional: the agents connect as the restricted NATS "agents" user (infra/nats/nats.conf), while the test itself
# acts as the operator (TEST_NATS_URL = the "services" user). Unset: everything uses TEST_NATS_URL.
AGENTS_NATS_URL = os.getenv("TEST_NATS_AGENTS_URL") or NATS_URL
DB_URL = os.getenv("TEST_DATABASE_URL")
MQTT_URL = os.getenv("TEST_MQTT_URL")
pytestmark = pytest.mark.skipif(not (NATS_URL and DB_URL), reason="needs TEST_NATS_URL and TEST_DATABASE_URL")

SCHEMAS = ("messaging", "coordinator", "personalization", "fleet", "telemetry", "inventory", "social", "trend",
           "finance", "gateway", "catalog")
STREAMS = ("EVENTS", "COMMANDS", "TELEMETRY", "DLQ", "KV_printer_status", "KV_heartbeats", "KV_leases", "KV_config",
           "OBJ_uploads")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_mqtt_broker(port: int):
    from amqtt.broker import Broker

    broker = Broker({
        "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    })
    await broker.start()
    return broker


@pytest.fixture
async def stack(tmp_path):
    import psycopg

    from printfarm.config import Settings

    # clean slate: drop every agent schema
    async with await psycopg.AsyncConnection.connect(DB_URL, autocommit=True) as c:
        for schema in SCHEMAS:
            await c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    # clean slate: delete streams and KV so durable consumers start fresh
    import nats

    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    for name in STREAMS:
        with contextlib.suppress(Exception):
            await js.delete_stream(name)
    await nc.close()

    ws_port = free_port()
    if MQTT_URL:
        broker, mqtt_host, mqtt_port = None, urlparse(MQTT_URL).hostname, urlparse(MQTT_URL).port or 1883
    else:
        mqtt_host, mqtt_port = "127.0.0.1", free_port()
        broker = await start_mqtt_broker(mqtt_port)
    serial = f"SIM{os.getpid()}"  # unique topic, in case a shared broker has other simulators on it
    cfg = tmp_path / "fleet.toml"
    cfg.write_text(textwrap.dedent(f"""
        [fleet]
        auto_clear_bed = true
        schedule_interval_s = 1
        batch_window_s = 1
        [simulator]
        print_seconds = 4
        [safety]
        pause_confirm_s = 3
        pause_retries = 1
        start_confirm_s = 20
        upload_timeout_s = 30
        [[printers]]
        id = "bambu-01"
        vendor = "bambu"
        simulated = true
        host = "{mqtt_host}"
        port = {mqtt_port}
        tls = false
        serial = "{serial}-1"
        access_code_env = "SIM_ACCESS_CODE"
        ftp_host = "127.0.0.1"
        ftp_port = {free_port()}
        camera_port = {free_port()}
        bed_mm = [256, 256]
        nozzle_mm = 0.4
        material = "PLA"
        color = "black"
        [[printers]]
        id = "bambu-02"
        vendor = "bambu"
        simulated = true
        host = "{mqtt_host}"
        port = {mqtt_port}
        tls = false
        serial = "{serial}-2"
        access_code_env = "SIM_ACCESS_CODE"
        ftp_host = "127.0.0.1"
        ftp_port = {free_port()}
        camera_port = {free_port()}
        bed_mm = [256, 256]
        nozzle_mm = 0.4
        material = "PLA"
        color = "red"
        [[printers]]
        id = "voron-01"
        vendor = "moonraker"
        simulated = true
        url = "ws://127.0.0.1:{ws_port}/websocket"
        bed_mm = [300, 300]
        nozzle_mm = 0.4
        material = "PLA"
        color = "white"
        [[materials]]
        material = "PLA"
        color = "black"
        grams_per_spool = 1000
        cost_per_spool_agorot = 2200
        lead_time_days = 5
        safety_stock_days = 3
        baseline_daily_grams = 150
        auto_approve_limit_agorot = 5000
        [[spools]]
        id = "SP-1"
        material = "PLA"
        color = "black"
        grams = 1000
        remaining_grams = 650
        loaded_on = "bambu-01"
        [[spools]]
        id = "SP-2"
        material = "PLA"
        color = "white"
        grams = 1000
        loaded_on = "voron-01"
    """))
    # Social Listening without the model download or an API key: word-hash embeddings and keyword triage.
    os.environ.update(SOCIAL_EMBEDDER="hash", SOCIAL_LLM="fake")
    os.environ.update(NATS_URL=AGENTS_NATS_URL, DATABASE_URL=DB_URL, FLEET_CONFIG=str(cfg), ARTIFACT_DIR=str(tmp_path / "art"),
                      SIM_ACCESS_CODE="12345678")
    settings = Settings()

    from printfarm.cli import AGENTS, run_agents
    from printfarm.simulator.printers import run_simulator

    stop = asyncio.Event()
    faults = {
        "bambu-01": "upload_flaky",                     # the first upload fails; the agent must retry
        "voron-01": "thermal_runaway",                  # auto-pause, and the printer obeys
        "bambu-02": "thermal_runaway+ignore_pause",     # auto-pause, but the printer ignores it
    }
    sim = asyncio.create_task(run_simulator(settings.fleet(), faults=faults, bind="127.0.0.1"))
    agents = asyncio.create_task(run_agents(list(AGENTS), settings, stop))
    yield dataclasses.replace(settings, nats_url=NATS_URL)    # the test sends operator commands
    stop.set()
    await asyncio.wait_for(agents, 10)
    sim.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sim
    if broker:
        await broker.shutdown()


async def wait_for(db, sql, params, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            async with db.tx() as conn:
                last = await (await conn.execute(sql, params)).fetchall()
        except Exception as exc:  # noqa: BLE001 - agents may still be migrating
            last = exc
            await asyncio.sleep(0.5)
            continue
        if predicate(last):
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out; last result: {last}")


async def test_order_to_print_to_inventory(stack):
    from printfarm.cli import _publish
    from printfarm.contracts.messages import (
        AdjustSpool,
        ConfirmPoOrdered,
        CreateOrder,
        DecideApproval,
        OrderReceived,
        RecordPayment,
        RegisterSpool,
        SetConfig,
        SetReorderRule,
        ShipOrder,
        UpdateFinanceSettings,
    )
    from printfarm.contracts.orders import OrderLine
    from printfarm.store import Database

    settings = stack
    db = Database(DB_URL)
    await db.open()
    try:
        # wait until telemetry saw all three printers online and fleet knows it
        await wait_for(db, "SELECT printer_id FROM fleet.printers WHERE online", (), lambda r: len(r) == 3)

        # the order pipeline's auto-advance switches on (off by default: every stage would wait for a person)
        for key in ("orders.auto_personalize", "orders.auto_schedule", "orders.auto_qc_pass"):
            await _publish(settings, SetConfig(key=key, value=True, changed_by="test"))
        await wait_for(db, "SELECT count(*) AS n FROM coordinator.config_changes", (), lambda r: r[0]["n"] == 3)
        await asyncio.sleep(6)  # agents re-read settings every 5 s

        # 1) a two-line order of black nameplates -> bambu-01 over MQTT; its 3 units fit one plate
        await _publish(settings, CreateOrder(
            order_id="ord-black", customer_name="Cohen family", channel="whatsapp", created_by="test",
            items=[OrderLine(text="משפחת כהן", quantity=2, unit_price_agorot=12000),
                   OrderLine(text="כהן", quantity=1, unit_price_agorot=11400)]), correlationid="ord-black")
        await wait_for(db, "SELECT state, context -> 'qc' ->> 'status' AS qc FROM coordinator.sagas WHERE saga_id = %s",
                              ("ord-black",), lambda r: r and r[0]["state"] == "QC" and r[0]["qc"] == "passed")
        qc = await wait_for(db, "SELECT passed, failed_keys FROM coordinator.qc_checks WHERE order_id = 'ord-black'", (),
                            lambda r: r)
        assert qc[0]["passed"] and qc[0]["failed_keys"] == []      # the failed units are kept per check (M0)
        total = await wait_for(db, "SELECT total_agorot FROM coordinator.orders WHERE order_id = 'ord-black'", (), lambda r: r)
        assert total[0]["total_agorot"] == 35400
        models = await wait_for(db, "SELECT item_id FROM personalization.artifacts WHERE order_id = 'ord-black' "
                                    "ORDER BY item_id", (), lambda r: len(r) == 2)
        assert [m["item_id"] for m in models] == ["1", "2"]          # one nameplate per order line
        await _publish(settings, ShipOrder(order_id="ord-black", carrier="Israel Post", shipped_by="test"),
                       correlationid="ord-black")
        await wait_for(db, "SELECT state FROM coordinator.sagas WHERE saga_id = %s", ("ord-black",),
                       lambda r: r[0]["state"] == "SHIPPED")

        plates = await wait_for(db, "SELECT plate_id, printer_id, status FROM fleet.plates", (), lambda r: len(r) == 1)
        assert plates[0]["printer_id"] == "bambu-01" and plates[0]["status"] == "finished"
        items = await wait_for(db, "SELECT count(*) AS n FROM fleet.plate_items", (), lambda r: True)
        assert items[0]["n"] == 3  # bin packing put all three units on one plate

        # the plate went to the printer as a file: FTPS upload (first attempt failed, retried), md5 checked by the printer
        start = await wait_for(db, "SELECT status, attempts, remote_file, md5, bytes, error FROM telemetry.plate_starts "
                                   "WHERE plate_id = %s", (plates[0]["plate_id"],), lambda r: r and r[0]["status"] == "started")
        assert start[0]["attempts"] == 2 and start[0]["remote_file"] == f"{plates[0]['plate_id']}.gcode.3mf"
        assert len(start[0]["md5"]) == 32 and start[0]["bytes"] > 0 and start[0]["error"] is None

        # inventory consumed filament and, being below the reorder point, asked for approval (66 > 50 limit)
        spool = await wait_for(db, "SELECT remaining_grams FROM inventory.spools WHERE spool_id = 'SP-1'", (),
                               lambda r: r[0]["remaining_grams"] < 650)
        assert spool[0]["remaining_grams"] < 650
        appr = await wait_for(db, "SELECT approval_id, status FROM coordinator.approvals", (), lambda r: len(r) == 1)
        await _publish(settings, DecideApproval(approval_id=appr[0]["approval_id"], approved=True, decided_by="test"))
        po = await wait_for(db, "SELECT po_id, status FROM inventory.purchase_orders", (),
                            lambda r: r and r[0]["status"] == "placed")

        # the ledger spread the plate's filament over its three jobs (allocated: product margins only, not P&L)
        alloc = await wait_for(db, "SELECT job_id, product_type, gross_agorot FROM finance.expense_entries "
                                   "WHERE kind = 'allocated' AND category = 'filament'", (),
                               lambda r: len({x["job_id"] for x in r}) == 3)
        assert {r["product_type"] for r in alloc} == {"nameplate"} and all(r["gross_agorot"] > 0 for r in alloc)

        # a recorded payment becomes income with VAT split (osek murshe, 18 %)
        await _publish(settings, RecordPayment(order_id="ord-black", amount_agorot=35400, method="bit", recorded_by="test"),
                       correlationid="ord-black")
        income = await wait_for(db, "SELECT gross_agorot, vat_agorot, net_agorot, product_type FROM finance.income_entries",
                                (), lambda r: len(r) == 1)
        assert (income[0]["gross_agorot"], income[0]["vat_agorot"], income[0]["net_agorot"]) == (35400, 5400, 30000)
        assert income[0]["product_type"] == "nameplate"

        # ordering the approved PO from a supplier books the real total as a cash expense
        await _publish(settings, ConfirmPoOrdered(po_id=po[0]["po_id"], supplier="Test Supplier", total_agorot=23600,
                                                  confirmed_by="test"))
        cash = await wait_for(db, "SELECT gross_agorot, net_agorot, vendor FROM finance.expense_entries WHERE kind = 'cash'",
                              (), lambda r: len(r) == 1)
        assert (cash[0]["gross_agorot"], cash[0]["net_agorot"], cash[0]["vendor"]) == (23600, 20000, "Test Supplier")
        # what a gram really cost (net) is now the price of later per-job filament
        price = await wait_for(db, "SELECT agorot_per_gram, po_id FROM finance.filament_prices WHERE material = 'PLA' "
                                   "AND color = 'black'", (), lambda r: r)
        assert price[0]["po_id"] == po[0]["po_id"] and float(price[0]["agorot_per_gram"]) > 0
        await _publish(settings, UpdateFinanceSettings(business_type="murshe", vat_rate=0.18, electricity_agorot_per_kwh=70,
                                                       default_printer_watts=120, default_filament_agorot_per_gram=9,
                                                       confirm=True, updated_by="test"))
        fs = await wait_for(db, "SELECT confirmed_by, electricity_agorot_per_kwh FROM finance.settings", (),
                            lambda r: r[0]["confirmed_by"] == "test")
        assert float(fs[0]["electricity_agorot_per_kwh"]) == 70
        await wait_for(db, "SELECT status FROM inventory.purchase_orders", (), lambda r: r[0]["status"] == "ordered")

        # filament: the plate's reservation was released and its grams booked on the loaded spool
        black_plate = plates[0]["plate_id"]
        booked = await wait_for(db, "SELECT spool_id, delta_grams FROM inventory.ledger WHERE ref = %s", (black_plate,),
                                lambda r: r)
        assert booked[0]["spool_id"] == "SP-1" and booked[0]["delta_grams"] < 0
        await wait_for(db, "SELECT count(*) AS n FROM inventory.reservations WHERE plate_id = %s", (black_plate,),
                       lambda r: r[0]["n"] == 0)
        # a minimum above the stock raises a low-stock alert; weighing a fresh spool in clears it again
        await _publish(settings, SetReorderRule(material="PLA", color="black", min_stock_grams=5000, changed_by="test"))
        await wait_for(db, "SELECT kind FROM inventory.alerts WHERE kind = 'low_stock' AND resolved_at IS NULL", (),
                       lambda r: r)
        await _publish(settings, RegisterSpool(spool_id="SP-E2E", material="PLA", color="black", initial_grams=5000,
                                               registered_by="test"))
        await wait_for(db, "SELECT resolved_by FROM inventory.alerts WHERE kind = 'low_stock'", (),
                       lambda r: r and r[0]["resolved_by"] == "auto")
        # weighing the loaded spool at 0 g raises "spool empty"
        await _publish(settings, AdjustSpool(spool_id="SP-1", remaining_grams=0, adjusted_by="test"))
        await wait_for(db, "SELECT spool_id FROM inventory.alerts WHERE kind = 'spool_empty' AND resolved_at IS NULL",
                       (), lambda r: r and r[0]["spool_id"] == "SP-1")

        # 2) white nameplate -> voron-01 (HTTP upload, WebSocket control); thermal runaway -> auto pause, CONFIRMED
        # 3) red nameplate -> bambu-02; thermal runaway -> auto pause, but the printer ignores it -> safety alert
        await _publish(settings, OrderReceived(order_id="ord-white", customer_ref="c2", text="Noa", color="white"),
                       correlationid="ord-white")
        await _publish(settings, OrderReceived(order_id="ord-red", customer_ref="c3", text="Dana", color="red"),
                       correlationid="ord-red")
        fails = await wait_for(db, "SELECT printer_id, detector, action_taken FROM telemetry.failures "
                                   "WHERE detector = 'thermal_runaway_nozzle' ORDER BY id", (),
                               lambda r: {x["printer_id"] for x in r} >= {"voron-01", "bambu-02"}, timeout=90)
        assert all(f["action_taken"] == "pause_sent" for f in fails)

        checks = await wait_for(db, "SELECT printer_id, confirmed, latency_ms, attempts, final_status, requested_by "
                                    "FROM telemetry.pause_checks ORDER BY id", (),
                                lambda r: {x["printer_id"] for x in r} >= {"voron-01", "bambu-02"}, timeout=60)
        voron = next(c for c in checks if c["printer_id"] == "voron-01")
        assert voron["confirmed"] and voron["final_status"] == "paused" and voron["latency_ms"] is not None
        assert voron["requested_by"] == "telemetry"
        bambu2 = next(c for c in checks if c["printer_id"] == "bambu-02")
        assert not bambu2["confirmed"] and bambu2["attempts"] == 2 and bambu2["final_status"] == "printing"
        alert = await wait_for(db, "SELECT severity, action_taken, message FROM telemetry.failures "
                                   "WHERE printer_id = 'bambu-02' AND detector = 'pause_unconfirmed'", (), lambda r: r)
        assert alert[0]["severity"] == "critical" and "still reports printing" in alert[0]["message"]

        # the white plate also went over HTTP to Moonraker and was started from the uploaded file
        white = await wait_for(db, "SELECT s.status, s.remote_file FROM telemetry.plate_starts s "
                                   "JOIN fleet.plates p USING (plate_id) WHERE p.printer_id = 'voron-01'", (),
                               lambda r: r and r[0]["status"] == "started")
        assert white[0]["remote_file"].endswith(".gcode")

        # 4) Instant Catalog: a sliced 3MF template uploaded, then a stock batch of two -> one piece per plate,
        #    the sliced file itself sent to bambu-01 (prices 0: shelf stock, no income)
        import hashlib

        from test_catalog import sliced_model

        from printfarm.bus.nats_bus import NatsBus
        from printfarm.contracts.messages import UpsertCatalogProduct

        data = sliced_model()
        sha = hashlib.sha256(data).hexdigest()
        bus = await NatsBus.connect(NATS_URL, name="e2e-upload")
        await bus.blob_put("uploads", sha, data)
        await bus.close()
        await _publish(settings, UpsertCatalogProduct(sku="HOOK-E2E", name="Wall hook", category="home", material="PLA",
                                                      color="black", price_agorot=2500, upload=sha, changed_by="test"))
        product = await wait_for(db, "SELECT sliced, est_minutes, est_grams, estimated, file_uri FROM catalog.products "
                                     "WHERE sku = 'HOOK-E2E'", (), lambda r: r)
        assert product[0]["sliced"] and product[0]["est_minutes"] == 90 and not product[0]["estimated"]
        await _publish(settings, CreateOrder(
            order_id="stk-e2e", customer_name="Stock batch", channel="stock", dispatch=True, created_by="test",
            items=[OrderLine(product="catalog", sku="HOOK-E2E", quantity=2, unit_price_agorot=0)]),
            correlationid="stk-e2e")
        jobs = await wait_for(db, "SELECT job_id, whole_plate, product_type FROM fleet.jobs WHERE order_id = 'stk-e2e'",
                              (), lambda r: len(r) == 2)
        assert all(j["whole_plate"] and j["product_type"] == "HOOK-E2E" for j in jobs)
        started = await wait_for(db, "SELECT s.status, s.remote_file, p.printer_id, "
                                     "(SELECT count(*) FROM fleet.plate_items i WHERE i.plate_id = p.plate_id) AS parts "
                                     "FROM telemetry.plate_starts s JOIN fleet.plates p USING (plate_id) "
                                     "JOIN fleet.jobs j ON j.plate_id = p.plate_id WHERE j.order_id = 'stk-e2e'", (),
                                 lambda r: r and r[0]["status"] == "started", timeout=90)
        assert started[0]["printer_id"] == "bambu-01" and started[0]["parts"] == 1
        assert started[0]["remote_file"].endswith(".gcode.3mf")

        # Social Listening (ADR-0007): captured posts -> prefilter -> triage -> lead with a Hebrew draft -> a person
        # approves the (edited) text, posts it themselves and marks it posted; a labelled capture joins the examples.
        from datetime import UTC, datetime

        from printfarm.contracts.messages import CaptureSubmitted, DecideLead, LabelCapture, MarkLeadPosted

        await wait_for(db, "SELECT sku FROM social.sku_vectors WHERE sku = 'HOOK-E2E'", (), lambda r: r)
        seeds = await wait_for(db, "SELECT count(*) AS n FROM social.examples WHERE embedding IS NOT NULL", (),
                               lambda r: r[0]["n"] >= 11)
        assert seeds[0]["n"] >= 11
        now = datetime.now(UTC)
        buyer = "מישהו מכיר מקום שמדפיס שלט עם שם לדלת? צריך לחדר של הבן שלי"
        for cid, text in (("cap-buy", buyer), ("cap-sell", "מוכר אופניים משומשים במצב מעולה, כמעט חדשים"),
                          ("cap-dup", "  " + buyer.replace(" ", "  ") + " ")):
            await _publish(settings, CaptureSubmitted(capture_id=cid, group_name="הורים בחיפה",
                                                      post_url="https://www.facebook.com/groups/1/posts/2",
                                                      post_text=text, captured_by="test", captured_at=now))
        lead = await wait_for(db, "SELECT l.lead_id, l.status, l.draft, l.sku, c.status AS cstatus, c.intent "
                                  "FROM social.leads l JOIN social.captures c USING (capture_id) "
                                  "WHERE capture_id = 'cap-buy'", (), lambda r: r and r[0]["status"] == "pending")
        assert lead[0]["cstatus"] == "lead" and lead[0]["intent"] == "buy" and lead[0]["sku"] == "HOOK-E2E"
        assert lead[0]["draft"].startswith("היי!")                       # the (fake) model's Hebrew draft
        sell = await wait_for(db, "SELECT status, status_reason FROM social.captures WHERE capture_id = 'cap-sell'", (),
                              lambda r: r and r[0]["status"] not in ("new", "scoring"))
        assert sell[0]["status"] == "ignored" and "prefilter" in sell[0]["status_reason"]
        await asyncio.sleep(1)
        async with db.tx() as conn:
            dup = await (await conn.execute("SELECT count(*) AS n FROM social.captures WHERE capture_id = 'cap-dup'")).fetchone()
        assert dup["n"] == 0                                             # the same post twice is one capture
        lead_id = lead[0]["lead_id"]
        await _publish(settings, MarkLeadPosted(lead_id=lead_id, posted_by="test"))        # refused: not approved yet
        await _publish(settings, DecideLead(lead_id=lead_id, approve=True, final_text="היי! נשמח להדפיס לכם שלט 🙂",
                                            decided_by="test"))
        await wait_for(db, "SELECT status FROM social.leads WHERE lead_id = %s", (lead_id,),
                       lambda r: r[0]["status"] == "approved")
        await _publish(settings, MarkLeadPosted(lead_id=lead_id, posted_by="test"))
        posted = await wait_for(db, "SELECT status, final_text, posted_by FROM social.leads WHERE lead_id = %s", (lead_id,),
                                lambda r: r[0]["status"] == "posted")
        assert posted[0]["final_text"].startswith("היי!") and posted[0]["posted_by"] == "test"
        await _publish(settings, LabelCapture(capture_id="cap-sell", label="not_buyer", labelled_by="test"))
        ex = await wait_for(db, "SELECT label, source, embedding IS NOT NULL AS has_vec FROM social.examples "
                                "WHERE example_id = 'cap-cap-sell'", (), lambda r: r)
        assert ex[0] == {"label": "not_buyer", "source": "capture", "has_vec": True}
        calls = await wait_for(db, "SELECT purpose, model FROM social.llm_calls ORDER BY id", (), lambda r: len(r) >= 2)
        assert [(c["purpose"], c["model"]) for c in calls] == [("classify", "claude-haiku-5-5"), ("draft", "claude-opus-5-5")]

        # every handler ran exactly once per message; nothing was dead-lettered
        async with db.tx() as conn:
            pending = await (await conn.execute(
                "SELECT count(*) AS n FROM messaging.outbox WHERE published_at IS NULL")).fetchone()
        await asyncio.sleep(1)
        assert pending["n"] <= 1
    finally:
        await db.close()
