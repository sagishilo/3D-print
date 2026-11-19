"""Migration 014 on a real Postgres: the shelf constraints, item alerts allowed, and the cost of a piece sold from
the shelf (reporting.stock_sales_v: stock-batch costs per piece built). Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")


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


async def test_a_piece_sold_from_the_shelf_costs_what_building_one_cost(conn):
    # a stock batch of 4 pieces of SKU-PG: 400 agorot of filament allocated to its jobs
    await conn.execute("INSERT INTO coordinator.orders (order_id, customer_name, channel, subtotal_agorot, total_agorot, "
                       "units, created_by) VALUES ('stk-pg', 'Stock batch', 'stock', 0, 0, 4, 't'), "
                       "('ord-pg', 'Noa', 'manual', 5000, 5000, 2, 't')")
    await conn.execute("INSERT INTO coordinator.order_items (order_id, item_id, product, sku, text, material, color, "
                       "quantity, unit_price_agorot, line_total_agorot, niche) VALUES "
                       "('stk-pg', '1', 'catalog', 'SKU-PG', 'x', 'PLA', 'black', 4, 0, 0, 'home'), "
                       "('ord-pg', '1', 'catalog', 'SKU-PG', 'x', 'PLA', 'black', 2, 2500, 5000, 'home')")
    await conn.execute("INSERT INTO fleet.jobs (job_id, order_id, correlationid, artifact_uri, material, color, footprint_w, "
                       "footprint_d, est_grams, est_minutes, status) VALUES ('stk-pg-i1-u1-a1', 'stk-pg', 'stk-pg', "
                       "'file:///x', 'PLA', 'black', 10, 10, 5, 10, 'completed')")
    await conn.execute("INSERT INTO finance.expense_entries (source_event_id, entry_date, category, gross_agorot, net_agorot, "
                       "kind, job_id, product_type) VALUES ('pg-stk-cost', current_date, 'filament', 400, 400, 'allocated', "
                       "'stk-pg-i1-u1-a1', 'SKU-PG')")
    await conn.execute("INSERT INTO coordinator.fg_ledger (sku, delta_on_hand, reason, order_id) VALUES "
                       "('SKU-PG', 4, 'built', 'stk-pg'), ('SKU-PG', -2, 'sold', 'ord-pg')")
    row = await (await conn.execute("SELECT units, cost_agorot, niche FROM reporting.stock_sales_v WHERE order_id = 'ord-pg'")
                 ).fetchone()
    assert row == {"units": 2, "cost_agorot": 200, "niche": "home"}


async def test_shelf_and_item_constraints(conn):
    import psycopg

    await conn.execute("INSERT INTO inventory.alerts (alert_id, kind, severity, subject, material, color, message) "
                       "VALUES ('pg-a1', 'low_item', 'warning', 'item/box', '', '', 'low')")
    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute("INSERT INTO coordinator.finished_goods (sku, on_hand, reserved) VALUES ('BAD-PG', 1, 2)")
