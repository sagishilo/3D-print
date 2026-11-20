"""printfarm reset-data on a real Postgres: demo and test data go, the owner, settings, finance settings (minus
their confirmation), materials and the built-in niches stay; it refuses when no active owner would be left.
Runs when TEST_DATABASE_URL is set."""

from __future__ import annotations

import os

import pytest

from printfarm.store import reset

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")


@pytest.fixture
async def db():
    from printfarm.store.db import Database

    d = Database(DB_URL)
    await d.open()
    await d.migrate()
    yield d
    async with d.tx() as conn:                       # leave nothing for the other tests (seeded niches are counted)
        await conn.execute("DELETE FROM catalog.niches WHERE slug = 'pets-reset'")
        await conn.execute("DELETE FROM gateway.users WHERE username = 'op-reset1'")
        await conn.execute("DELETE FROM fleet.printers WHERE printer_id = 'reset-p1'")
    await d.close()


async def one(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchone()


async def seed(db):
    async with db.tx() as conn:
        for name, role in (("admin", "owner"), ("op-reset1", "operator")):
            await conn.execute(
                "INSERT INTO gateway.users (username, display_name, role, password_hash) VALUES (%s, %s, %s, 'x') "
                "ON CONFLICT ((lower(username))) DO UPDATE SET role = EXCLUDED.role, disabled = false", (name, name, role))
        await conn.execute("INSERT INTO catalog.niches (slug, name) VALUES ('pets-reset', 'Pets') ON CONFLICT DO NOTHING")
        await conn.execute("INSERT INTO fleet.printers (printer_id, vendor, bed_w, bed_d, nozzle_mm, material, color) "
                           "VALUES ('reset-p1', 'bambu', 256, 256, 0.4, 'PLA', 'black') ON CONFLICT DO NOTHING")
        await conn.execute("INSERT INTO finance.settings (id) VALUES (1) ON CONFLICT DO NOTHING")
        await conn.execute("UPDATE finance.settings SET confirmed_by = 'admin', confirmed_at = now(), vat_rate = 0.18")


async def test_reset_removes_test_data_and_keeps_the_owner_and_settings(db):
    await seed(db)
    p = reset.plan(keep_users=("admin",))
    async with db.tx() as conn:
        await reset.survey(conn, p)
    assert "op-reset1" in p.users_removed and "admin" not in p.users_removed
    assert "pets-reset" in p.niches_removed and p.counts["fleet.printers"] >= 1
    assert any("fleet.printers" in line for line in p.lines())

    async with db.tx() as conn:
        await reset.apply(conn, p)
    assert (await one(db, "SELECT count(*) AS n FROM fleet.printers"))["n"] == 0
    assert (await one(db, "SELECT count(*) AS n FROM catalog.products"))["n"] == 0
    assert await one(db, "SELECT 1 FROM gateway.users WHERE username = 'op-reset1'") is None
    assert await one(db, "SELECT 1 FROM gateway.users WHERE username = 'admin' AND role = 'owner'")
    assert await one(db, "SELECT 1 FROM catalog.niches WHERE slug = 'pets-reset'") is None
    assert (await one(db, "SELECT count(*) AS n FROM catalog.niches WHERE slug = ANY(%s)",
                      list(reset.SEED_NICHES)))["n"] == len(reset.SEED_NICHES)
    fin = await one(db, "SELECT confirmed_by, confirmed_at, vat_rate FROM finance.settings")
    assert fin["confirmed_by"] is None and fin["confirmed_at"] is None and float(fin["vat_rate"]) == 0.18
    audit = await one(db, "SELECT actor, subject FROM gateway.audit_log")
    assert audit["subject"] == "system.reset_data"


async def test_keep_catalog_keeps_products_and_niches(db):
    await seed(db)
    p = reset.plan(keep_users=("admin",), keep_catalog=True)
    assert "catalog.products" not in p.tables
    async with db.tx() as conn:
        await reset.survey(conn, p)
        await reset.apply(conn, p)
    assert await one(db, "SELECT 1 FROM catalog.niches WHERE slug = 'pets-reset'")


async def test_it_refuses_when_no_active_owner_would_be_left(db):
    await seed(db)
    p = reset.plan(keep_users=("op-reset1",))
    async with db.tx() as conn:
        with pytest.raises(SystemExit, match="none of the users kept"):
            await reset.survey(conn, p)
