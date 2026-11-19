"""Migration 013 on a real Postgres: the seeded niches, the income backfill (split per line, adds up exactly, the
remainder on the last line), and the reporting views. Runs when TEST_DATABASE_URL is set (the CI e2e job)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")
MIGRATION = Path(__file__).parents[1] / "src/printfarm/store/migrations/013_niches.sql"


def backfill_sql() -> list[str]:
    """The two INSERT INTO finance.income_lines statements of the migration (run again on test data)."""
    text = re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))
    return [s.strip() for s in text.split(";") if "INSERT INTO finance.income_lines" in s]


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
    await c.set_autocommit(False)
    yield c
    await c.rollback()                                    # leave the database as the migration left it
    await c.close()


async def test_seeded_niches(conn):
    rows = await (await conn.execute("SELECT slug, qc_checklist FROM catalog.niches ORDER BY sort")).fetchall()
    assert [r["slug"] for r in rows] == ["home", "kids", "mechanical", "gifts", "unassigned"]
    assert rows[3]["qc_checklist"][0].startswith("Text spelled")


async def test_backfill_splits_existing_income_per_line(conn):
    await conn.execute("INSERT INTO coordinator.orders (order_id, customer_name, channel, subtotal_agorot, total_agorot, "
                       "units, created_by) VALUES ('m13', 'Noa', 'manual', 10001, 10001, 3, 't')")
    await conn.execute("INSERT INTO coordinator.sagas (saga_id, saga_type, state, context) "
                       "VALUES ('m13', 'order', 'SHIPPED', '{}')")
    for item, product, sku, niche, total in (("1", "nameplate", "", "gifts", 4500), ("2", "catalog", "HOOK-M13", "home", 4500),
                                             ("3", "catalog", "TOY-M13", "kids", 1001)):
        await conn.execute(
            "INSERT INTO coordinator.order_items (order_id, item_id, product, sku, text, material, color, quantity, "
            "unit_price_agorot, line_total_agorot, niche) VALUES ('m13', %s, %s, %s, 'x', 'PLA', 'black', 1, %s, %s, %s)",
            (item, product, sku, total, total, niche))
    entry = (await (await conn.execute(
        "INSERT INTO finance.income_entries (source_event_id, order_id, entry_date, method, gross_agorot, net_agorot) "
        "VALUES ('m13-pay', 'm13', current_date, 'cash', 8475, 8475) RETURNING id")).fetchone())["id"]
    alone = (await (await conn.execute(
        "INSERT INTO finance.income_entries (source_event_id, entry_date, method, gross_agorot, net_agorot, product_type) "
        "VALUES ('m13-market', current_date, 'cash', 3000, 3000, 'market') RETURNING id")).fetchone())["id"]
    await conn.execute("DELETE FROM finance.income_lines")      # as before the migration (rolled back after)
    for sql in backfill_sql():
        await conn.execute(sql)
    split = await (await conn.execute("SELECT line_no, product_type, niche, net_agorot FROM finance.income_lines "
                                      "WHERE entry_id = %s ORDER BY line_no", (entry,))).fetchall()
    assert [(r["product_type"], r["niche"]) for r in split] == [("nameplate", "gifts"), ("HOOK-M13", "home"),
                                                                ("TOY-M13", "kids")]
    assert sum(r["net_agorot"] for r in split) == 8475
    assert [r["net_agorot"] for r in split][:2] == [round(8475 * 4500 / 10001)] * 2
    market = await (await conn.execute("SELECT niche, product_type, net_agorot FROM finance.income_lines "
                                       "WHERE entry_id = %s", (alone,))).fetchall()
    assert market == [{"niche": "unassigned", "product_type": "market", "net_agorot": 3000}]
    # the read models
    by_niche = {r["niche"]: int(r["net"]) for r in await (await conn.execute(
        "SELECT niche, sum(net_agorot) AS net FROM reporting.niche_revenue_v WHERE order_id = 'm13' GROUP BY 1")).fetchall()}
    assert sum(by_niche.values()) == 8475 and set(by_niche) == {"gifts", "home", "kids"}
    lines = await (await conn.execute("SELECT product_type, stock FROM reporting.order_lines_v WHERE order_id = 'm13' "
                                      "ORDER BY item_id")).fetchall()
    assert [r["product_type"] for r in lines] == ["nameplate", "HOOK-M13", "TOY-M13"] and not lines[0]["stock"]


async def test_a_job_cost_finds_its_order_lines_niche(conn):
    await conn.execute("INSERT INTO coordinator.orders (order_id, customer_name, channel, subtotal_agorot, total_agorot, "
                       "units, created_by) VALUES ('m13c', 'Dan', 'manual', 100, 100, 1, 't')")
    await conn.execute(
        "INSERT INTO coordinator.order_items (order_id, item_id, product, sku, text, material, color, quantity, "
        "unit_price_agorot, line_total_agorot, niche) VALUES ('m13c', '2', 'catalog', 'TOY-C', 'x', 'PLA', 'red', 1, 100, "
        "100, 'kids')")
    await conn.execute(
        "INSERT INTO fleet.jobs (job_id, order_id, correlationid, artifact_uri, material, color, footprint_w, footprint_d, "
        "est_grams, est_minutes, status) VALUES ('m13c-i2-u1-a0', 'm13c', 'm13c', 'file:///x', 'PLA', 'red', 10, 10, 5, "
        "10, 'completed')")
    await conn.execute(
        "INSERT INTO finance.expense_entries (source_event_id, entry_date, category, gross_agorot, net_agorot, kind, job_id, "
        "product_type) VALUES ('m13c-cost', current_date, 'filament', 42, 42, 'allocated', 'm13c-i2-u1-a0', 'TOY-C')")
    row = await (await conn.execute("SELECT niche, stock, net_agorot FROM reporting.sku_costs_v "
                                    "WHERE job_id = 'm13c-i2-u1-a0'")).fetchone()
    assert row == {"niche": "kids", "stock": False, "net_agorot": 42}
