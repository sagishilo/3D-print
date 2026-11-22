"""Migration 023 and the personalization agent on a real Postgres: a 3MF's preview kept as the product image (and
taken from the stored template for products imported before images), an uploaded picture that a later 3MF never
replaces, switching a product on and off, and deleting: refused while an order refers to the SKU, otherwise the
product, its tags, its image and its template files go. Runs when TEST_DATABASE_URL is set (the CI e2e job)."""

from __future__ import annotations

import hashlib
import os

import pytest
from test_catalog import NS, cube, three_mf

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="needs TEST_DATABASE_URL")

SKUS = ["PG-IMG-1", "PG-IMG-2", "PG-IMG-3"]
PNG = b"\x89PNG\r\n\x1a\n" + b"preview" * 4
JPEG = b"\xff\xd8\xff\xe0" + b"custom" * 4


def template(preview: bytes = PNG) -> bytes:
    return three_mf({"3D/3dmodel.model": f'<model {NS}><resources>{cube("1", 40, 20, 5)}</resources>'
                                         '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 50 50 0"/></build></model>',
                     "Metadata/plate_1.png": preview})


class Bus:
    """The uploads object store only (what the personalization agent reads)."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        self.blobs[sha] = data
        return sha

    async def blob_get(self, bucket, name):
        return self.blobs.get(name)


@pytest.fixture
async def db():
    from printfarm.store.db import Database

    d = Database(DB_URL)
    await d.open()
    await d.migrate()

    async def clean():
        async with d.tx() as conn:
            await conn.execute("DELETE FROM coordinator.order_items WHERE order_id = 'pg-img-o'")
            await conn.execute("DELETE FROM coordinator.orders WHERE order_id = 'pg-img-o'")
            await conn.execute("DELETE FROM catalog.products WHERE sku = ANY(%s)", (SKUS,))
            await conn.execute("DELETE FROM messaging.outbox WHERE producer = 'personalization'")

    await clean()
    yield d
    await clean()
    await d.close()


@pytest.fixture
def agent(db, tmp_path):
    from printfarm.agents.personalization.agent import PersonalizationAgent
    from printfarm.config import FleetCfg, Settings

    a = PersonalizationAgent(Bus(), db, Settings(database_url=DB_URL, artifact_dir=str(tmp_path)),
                             FleetCfg(printers=[], materials=[], spools=[]))
    return a


async def run(agent, cmd):
    from printfarm.contracts import new_message

    env = new_message(cmd, source="/test")
    async with agent.db.tx() as conn:
        await getattr(agent, HANDLERS[type(cmd).__name__])(env, cmd, conn)


HANDLERS = {"UpsertCatalogProduct": "upsert_product", "SetCatalogProductActive": "set_product_active",
            "DeleteCatalogProduct": "delete_product"}


async def one(db, sql, *params):
    async with db.tx() as conn:
        return await (await conn.execute(sql, params)).fetchone()


async def events(db, subject):
    async with db.tx() as conn:
        rows = await (await conn.execute(
            "SELECT envelope FROM messaging.outbox WHERE producer = 'personalization' AND subject = %s ORDER BY seq",
            (subject,))).fetchall()
    return [r["envelope"]["data"] for r in rows]


def upsert(sku: str, **kw):
    from printfarm.contracts.messages import UpsertCatalogProduct

    return UpsertCatalogProduct(**{"sku": sku, "name": "Card stand", "category": "stands", "price_agorot": 2900,
                                   "est_grams": 20, "est_minutes": 60, "niche": "unassigned", "changed_by": "noa", **kw})


async def test_an_imported_3mf_brings_its_preview_and_an_uploaded_picture_wins(agent, db):
    sha = agent.bus.put(template())
    await run(agent, upsert("PG-IMG-1", upload=sha, upload_name="stand.3mf"))
    img = await one(db, "SELECT mime, data, source, sha256 FROM catalog.product_images WHERE sku = %s", "PG-IMG-1")
    assert (img["mime"], bytes(img["data"]), img["source"]) == ("image/png", PNG, "3mf")
    assert img["sha256"] == hashlib.sha256(PNG).hexdigest()

    # an uploaded picture replaces it ...
    pic = agent.bus.put(JPEG)
    await run(agent, upsert("PG-IMG-1", image_upload=pic))
    img = await one(db, "SELECT mime, data, source FROM catalog.product_images WHERE sku = %s", "PG-IMG-1")
    assert (img["mime"], bytes(img["data"]), img["source"]) == ("image/jpeg", JPEG, "upload")
    # ... and a new 3MF template later does not
    other = agent.bus.put(template(PNG + b"v2"))
    await run(agent, upsert("PG-IMG-1", upload=other))
    assert (await one(db, "SELECT source FROM catalog.product_images WHERE sku = %s", "PG-IMG-1"))["source"] == "upload"

    # something that is not a picture is refused, and nothing changes
    bad = agent.bus.put(b"<svg onload=alert(1)/>")
    await run(agent, upsert("PG-IMG-1", image_upload=bad, name="Renamed"))
    assert (await one(db, "SELECT name FROM catalog.products WHERE sku = %s", "PG-IMG-1"))["name"] == "Card stand"
    rejected = await events(db, "evt.catalog.product.rejected")
    assert rejected[-1]["reason"] == "the picture is not a PNG, JPEG or WebP image"


async def test_a_product_imported_before_images_gets_its_preview_on_the_next_save(agent, db):
    sha = agent.bus.put(template())
    await run(agent, upsert("PG-IMG-2", upload=sha))
    async with db.tx() as conn:
        await conn.execute("DELETE FROM catalog.product_images WHERE sku = 'PG-IMG-2'")   # as before migration 023
    await run(agent, upsert("PG-IMG-2", name="Card stand v2"))                         # no new file: the stored one
    img = await one(db, "SELECT data, source FROM catalog.product_images WHERE sku = %s", "PG-IMG-2")
    assert bytes(img["data"]) == PNG and img["source"] == "3mf"


async def test_switching_a_product_on_and_off(agent, db):
    from printfarm.contracts.messages import SetCatalogProductActive

    await run(agent, upsert("PG-IMG-1", upload=agent.bus.put(template()), active=False))
    await run(agent, SetCatalogProductActive(sku="PG-IMG-1", active=True, changed_by="noa"))
    row = await one(db, "SELECT active, updated_by FROM catalog.products WHERE sku = %s", "PG-IMG-1")
    assert row["active"] is True and row["updated_by"] == "noa"
    assert (await events(db, "evt.catalog.product.updated"))[-1]["active"] is True
    await run(agent, SetCatalogProductActive(sku="PG-IMG-1", active=False, changed_by="noa"))
    assert (await one(db, "SELECT active FROM catalog.products WHERE sku = %s", "PG-IMG-1"))["active"] is False
    await run(agent, SetCatalogProductActive(sku="PG-IMG-3", active=True, changed_by="noa"))     # no such SKU
    assert (await events(db, "evt.catalog.product.rejected"))[-1]["action"] == "active"


async def test_delete_keeps_a_product_with_orders_and_removes_one_without(agent, db, tmp_path):
    from printfarm.contracts.messages import DeleteCatalogProduct

    await run(agent, upsert("PG-IMG-1", upload=agent.bus.put(template()), tags=["gifts"]))
    await run(agent, upsert("PG-IMG-2", upload=agent.bus.put(template(PNG + b"2"))))
    async with db.tx() as conn:
        await conn.execute("INSERT INTO coordinator.orders (order_id, customer_name, channel, subtotal_agorot, "
                           "total_agorot, units, created_by) VALUES ('pg-img-o', 'Noa', 'manual', 2900, 2900, 1, 't')")
        await conn.execute("INSERT INTO coordinator.order_items (order_id, item_id, product, sku, text, material, color, "
                           "quantity, unit_price_agorot, line_total_agorot) VALUES ('pg-img-o', '1', 'catalog', "
                           "'PG-IMG-2', 'x', 'PLA', 'black', 1, 2900, 2900)")
    # sold once: it stays, with what to do instead
    await run(agent, DeleteCatalogProduct(sku="PG-IMG-2", changed_by="admin"))
    assert await one(db, "SELECT 1 FROM catalog.products WHERE sku = %s", "PG-IMG-2")
    refused = (await events(db, "evt.catalog.product.rejected"))[-1]
    assert refused["action"] == "delete" and "1 order" in refused["reason"] and "switch it off" in refused["reason"]

    folder = tmp_path / "catalog"
    mine = sorted(p.name for p in folder.iterdir() if p.name.startswith("PG-IMG-1-"))
    assert len(mine) == 1
    (folder / "PG-IMG-10-0123456789ab.3mf").write_bytes(b"another SKU's file")        # a SKU with the same prefix
    await run(agent, DeleteCatalogProduct(sku="PG-IMG-1", changed_by="admin"))
    for table in ("catalog.products", "catalog.product_tags", "catalog.product_images"):
        assert await one(db, f"SELECT 1 FROM {table} WHERE sku = %s", "PG-IMG-1") is None, table
    assert not [p for p in folder.iterdir() if p.name.startswith("PG-IMG-1-")]
    assert (folder / "PG-IMG-10-0123456789ab.3mf").is_file()
    assert (await events(db, "evt.catalog.product.deleted"))[-1] == {"sku": "PG-IMG-1", "name": "Card stand", "by": "admin"}


# ---------------------------------------------------------------- where it comes from, and the right to sell it

def maker_template(licence: str = "Standard Digital File License") -> bytes:
    return three_mf({"3D/3dmodel.model": f'<model {NS}><metadata name="Title">Pokemon card stand</metadata>'
                                         f'<metadata name="Designer">MaajkiPL</metadata><metadata name="License">{licence}'
                                         f'</metadata><resources>{cube("1", 40, 20, 5)}</resources>'
                                         '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 50 50 0"/></build></model>',
                     "Metadata/plate_1.png": PNG})


async def test_the_3mf_names_designer_and_licence_and_a_personal_use_file_stays_off(agent, db):
    from printfarm.contracts.messages import SetCatalogProductActive

    sha = agent.bus.put(maker_template())
    # on sale at once: refused, a personal-use licence
    await run(agent, upsert("PG-IMG-1", upload=sha, source_url="https://makerworld.com/en/models/111937-x?y=1"))
    assert await one(db, "SELECT 1 FROM catalog.products WHERE sku = %s", "PG-IMG-1") is None
    assert "does not allow selling prints" in (await events(db, "evt.catalog.product.rejected"))[-1]["reason"]
    # switched off: kept, with the file's designer and licence and the canonical page
    await run(agent, upsert("PG-IMG-1", upload=sha, active=False, source_url="https://makerworld.com/en/models/111937-x?y=1"))
    row = await one(db, "SELECT designer, licence, source_url, source_site, source_id, active FROM catalog.products "
                        "WHERE sku = %s", "PG-IMG-1")
    assert row == {"designer": "MaajkiPL", "licence": "Standard Digital File License", "active": False,
                   "source_url": "https://makerworld.com/en/models/111937", "source_site": "makerworld", "source_id": "111937"}
    # the switch: refused without the confirmation, recorded with it
    await run(agent, SetCatalogProductActive(sku="PG-IMG-1", active=True, changed_by="noa"))
    refused = (await events(db, "evt.catalog.product.rejected"))[-1]
    assert refused["action"] == "active" and "commercial licence" in refused["reason"]
    assert (await one(db, "SELECT active FROM catalog.products WHERE sku = %s", "PG-IMG-1"))["active"] is False
    await run(agent, SetCatalogProductActive(sku="PG-IMG-1", active=True, rights_confirmed=True, changed_by="admin"))
    row = await one(db, "SELECT active, rights_confirmed_by, rights_confirmed_at FROM catalog.products WHERE sku = %s", "PG-IMG-1")
    assert row["active"] is True and row["rights_confirmed_by"] == "admin" and row["rights_confirmed_at"] is not None
    # switching off is never stopped; a typed licence wins over the file's; "" clears the page
    await run(agent, SetCatalogProductActive(sku="PG-IMG-1", active=False, changed_by="noa"))
    await run(agent, upsert("PG-IMG-1", active=False, licence="Commercial License", designer="", source_url=""))
    row = await one(db, "SELECT designer, licence, source_url FROM catalog.products WHERE sku = %s", "PG-IMG-1")
    assert row == {"designer": "", "licence": "Commercial License", "source_url": None}
    await run(agent, SetCatalogProductActive(sku="PG-IMG-1", active=True, changed_by="noa"))     # now allowed
    assert (await one(db, "SELECT active FROM catalog.products WHERE sku = %s", "PG-IMG-1"))["active"] is True


async def test_an_own_design_without_a_licence_goes_on_sale_as_before(agent, db):
    await run(agent, upsert("PG-IMG-2", upload=agent.bus.put(template())))
    row = await one(db, "SELECT active, licence, designer FROM catalog.products WHERE sku = %s", "PG-IMG-2")
    assert row == {"active": True, "licence": "", "designer": ""}


async def test_products_imported_before_get_designer_and_licence_on_the_next_save(agent, db):
    await run(agent, upsert("PG-IMG-3", upload=agent.bus.put(maker_template("BY")), active=False))
    async with db.tx() as conn:
        await conn.execute("UPDATE catalog.products SET designer = '', licence = '' WHERE sku = 'PG-IMG-3'")
    await run(agent, upsert("PG-IMG-3", active=False))                                  # no file: the stored one
    row = await one(db, "SELECT designer, licence FROM catalog.products WHERE sku = %s", "PG-IMG-3")
    assert row == {"designer": "MaajkiPL", "licence": "BY"}
