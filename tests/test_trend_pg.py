"""Migration 016 and the Trend Analyst's roll-up on a real Postgres: one day of sales, income, job costs, print time,
QC and social activity becomes reporting.daily_sku / daily_niche; a second run changes nothing (idempotent); the
windows feed the trend rules. Runs when TEST_DATABASE_URL is set (the CI e2e job)."""

from __future__ import annotations

import os
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")
DAY = date(2026, 3, 10)                     # a past day nothing else writes to
NOON = "2026-03-10 12:00:00+02"


@pytest.fixture
async def conn():
    import psycopg
    from psycopg.rows import dict_row

    from printfarm.store.db import Database

    database = Database(DB_URL)
    await database.open()
    await database.migrate()
    await database.close()
    c = await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row)
    yield c
    await c.rollback()
    await c.close()


async def seed(conn) -> None:
    ex = conn.execute
    await ex("INSERT INTO catalog.products (sku, name, category, material, color, price_agorot, file_uri, file_sha256, sliced, "
             "footprint_w, footprint_d, est_grams, est_minutes, estimated, created_by, updated_by, niche) VALUES "
             "('TR-HOOK', 'Hook', 'home', 'PLA', 'black', 2500, 'file:///x', 'x', true, 10, 10, 20, 60, false, 't', 't', 'home')")
    await ex("INSERT INTO coordinator.orders (order_id, customer_name, channel, subtotal_agorot, total_agorot, units, "
             "created_by, created_at) VALUES ('tr-1', 'Noa', 'manual', 5000, 5000, 2, 't', %s)", (NOON,))
    await ex("INSERT INTO coordinator.sagas (saga_id, saga_type, state, context) VALUES ('tr-1', 'order', 'SHIPPED', '{}')")
    await ex("INSERT INTO coordinator.saga_history (saga_id, from_state, to_state, message_id, message_type, at) VALUES "
             "('tr-1', 'QC', 'SHIPPED', 'm-tr-1', 't', %s)", (NOON,))
    await ex("INSERT INTO coordinator.order_items (order_id, item_id, product, sku, text, material, color, quantity, "
             "unit_price_agorot, line_total_agorot, niche) VALUES ('tr-1', '1', 'catalog', 'TR-HOOK', 'Hook', 'PLA', 'black', "
             "2, 2500, 5000, 'home')")
    entry = (await (await ex("INSERT INTO finance.income_entries (source_event_id, order_id, entry_date, method, gross_agorot, "
                             "net_agorot) VALUES ('tr-pay', 'tr-1', %s, 'cash', 5000, 4237) RETURNING id", (DAY,))).fetchone())["id"]
    await ex("INSERT INTO finance.income_lines (entry_id, line_no, order_id, product_type, niche, net_agorot) VALUES "
             "(%s, 1, 'tr-1', 'TR-HOOK', 'home', 4237)", (entry,))
    await ex("INSERT INTO fleet.jobs (job_id, order_id, correlationid, artifact_uri, material, color, footprint_w, footprint_d, "
             "est_grams, est_minutes, status, product_type, updated_at) VALUES "
             "('tr-1-i1-u1-a1', 'tr-1', 'tr-1', 'file:///x', 'PLA', 'black', 10, 10, 20, 60, 'completed', 'TR-HOOK', %s), "
             "('tr-1-i1-u2-a1', 'tr-1', 'tr-1', 'file:///x', 'PLA', 'black', 10, 10, 20, 60, 'completed', 'TR-HOOK', %s)",
             (NOON, NOON))
    await ex("INSERT INTO finance.expense_entries (source_event_id, entry_date, category, gross_agorot, net_agorot, kind, "
             "job_id, product_type) VALUES ('tr-c1', %s, 'filament', 170, 170, 'allocated', 'tr-1-i1-u1-a1', 'TR-HOOK'), "
             "('tr-c2', %s, 'electricity', 30, 30, 'allocated', 'tr-1-i1-u2-a1', 'TR-HOOK')", (DAY, DAY))
    await ex("INSERT INTO coordinator.qc_checks (order_id, passed, units_failed, units_checked, checked_by, checked_at, "
             "failed_keys) VALUES ('tr-1', false, 1, 2, 't', %s, '[\"1.2\"]')", (NOON,))
    await ex("INSERT INTO social.captures (capture_id, group_name, post_url, post_text, text_hash, captured_by, captured_at, "
             "received_at, "
             "status, sku_guess) VALUES ('tr-cap', 'g', '', 'looking for a hook', 'tr-hash', 't', %s, %s, 'lead', 'TR-HOOK')",
             (NOON, NOON))


async def test_one_day_rolls_up_and_a_rerun_changes_nothing(conn):
    from printfarm.agents.trend.agent import TrendAgent

    await seed(conn)
    stub = SimpleNamespace()
    for _ in range(2):                                   # idempotent: the second run replaces the first
        await TrendAgent.roll_day(stub, conn, DAY)
    rows = await (await conn.execute("SELECT * FROM reporting.daily_sku WHERE day = %s AND sku = 'TR-HOOK'", (DAY,))).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert (r["niche"], r["units_sold"], r["revenue_agorot"], r["cost_agorot"]) == ("home", 2, 4237, 200)
    assert (float(r["print_hours"]), r["qc_checked"], r["qc_failed"], r["captures"]) == (2.0, 2, 1, 1)
    niche = await (await conn.execute("SELECT units_sold, revenue_agorot FROM reporting.daily_niche WHERE day = %s AND "
                                      "niche = 'home'", (DAY,))).fetchone()
    assert niche["units_sold"] >= 2 and niche["revenue_agorot"] >= 4237
    assert (await (await conn.execute("SELECT rows FROM reporting.rollups WHERE day = %s", (DAY,))).fetchone())["rows"] >= 1


async def test_the_windows_feed_the_trend_rules(conn):
    from printfarm.agents.trend.agent import TrendAgent
    from printfarm.agents.trend.signals import detect

    for n, units in ((1, 6), (3, 5), (9, 2)):           # 11 sold this week, 2 the week before
        await conn.execute("INSERT INTO reporting.daily_sku (day, sku, niche, units_sold) VALUES (%s, 'TR-RISE', 'kids', %s)",
                           (DAY - timedelta(days=n), units))
    windows = await TrendAgent._windows(SimpleNamespace(), conn, DAY)
    mine = next(w for w in windows if w.scope == "sku" and w.subject == "TR-RISE")
    assert (mine.units_7d, mine.units_prev_7d, mine.niche) == (11, 2, "kids")
    assert any(s.subject == "TR-RISE" and s.direction == "rising" for s in detect(windows))
