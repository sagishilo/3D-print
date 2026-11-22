"""Gateway: the Instant Catalog read side, the 3MF upload, the batch estimate and catalog commands (Postgres faked)."""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import KEY, build_command, csrf, gateway_client, login  # noqa: E402
from test_catalog import plain_model, sliced_model  # noqa: E402

PRODUCT = {"sku": "HOOK-A", "name": "Wall hook", "category": "home", "material": "PLA", "color": "black",
           "price_agorot": 2500, "file_name": "hook.3mf", "sliced": True, "printer_model": "C12", "footprint_w": 30.0,
           "footprint_d": 30.0, "est_grams": 12.0, "est_minutes": 40.0, "estimated": False, "active": True,
           "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z", "updated_by": "admin",
           "niche": "home", "tags": []}


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def test_products_with_their_demand(client):
    db = client.app.state.db
    db.rows["FROM catalog.products p"] = [{**PRODUCT, "sold_7d": 12, "sold_prev_7d": 8, "in_production": 4}]
    db.rows["SELECT category, count(*) AS n FROM catalog.products AS p"] = [{"category": "home", "n": 1}]
    body = client.get("/api/v1/catalog/products").json()
    p = body["products"][0]
    assert p["trend_pct"] == 50.0 and p["footprint_mm"] == [30.0, 30.0] and p["in_production"] == 4
    assert body["counts"] == {"home": 1} and "stands" in body["categories"] and body["demand_days"] == 7
    assert client.get("/api/v1/catalog/products?category=weapons").status_code == 400
    assert client.get("/api/v1/catalog/products?sort=random").status_code == 400
    assert client.get("/api/v1/catalog/products/hook").status_code == 400


def test_upload_keeps_a_valid_3mf_and_refuses_anything_else(client):
    data = sliced_model()
    r = client.post("/api/v1/catalog/uploads?name=hook.3mf", content=data, headers=csrf(client))
    assert r.status_code == 201
    body = r.json()
    assert body["upload"] == hashlib.sha256(data).hexdigest() and body["info"]["sliced"] is True
    assert body["info"]["est_minutes"] == 90 and body["name"] == "hook.3mf"
    assert client.app.state.bus.kv["obs:uploads"][body["upload"]] == data
    bad = client.post("/api/v1/catalog/uploads", content=b"PK not really", headers=csrf(client))
    assert bad.status_code == 422 and "not a 3MF" in bad.json()["detail"]
    assert client.post("/api/v1/catalog/uploads", content=plain_model()).status_code == 403     # CSRF


def test_batch_estimate_counts_plates_and_the_filament_short(client):
    db = client.app.state.db
    db.rows["SELECT * FROM catalog.products WHERE sku = ANY"] = [
        PRODUCT, {**PRODUCT, "sku": "TAG-B", "sliced": False, "footprint_w": 40.0, "footprint_d": 20.0, "est_grams": 50.0}]
    db.rows["FROM fleet.printers ORDER BY bed_w"] = [{"bed_w": 256, "bed_d": 256}]
    db.rows["FROM inventory.spools WHERE status"] = [{"material": "PLA", "color": "black", "g": 700.0}]
    db.rows["FROM inventory.reservations GROUP"] = [{"material": "PLA", "color": "black", "g": 100.0}]
    db.rows["FROM fleet.jobs WHERE status = 'queued'"] = [{"material": "PLA", "color": "black", "g": 50.0}]
    est = client.get("/api/v1/catalog/estimate?items=HOOK-A:2&items=TAG-B:10").json()
    assert est["skus"] == 2 and est["pieces"] == 12 and est["plates"] == 2 + 1      # 2 sliced hooks alone, 10 tags share a plate
    assert est["materials"] == [{"material": "PLA", "color": "black", "need_grams": 524.0, "available_grams": 550.0,
                                 "short_grams": 0.0}]
    assert est["unsliced"] == ["TAG-B"]
    assert client.get("/api/v1/catalog/estimate?items=HOOK-A").status_code == 400
    db.rows["SELECT * FROM catalog.products WHERE sku = ANY"] = [PRODUCT]
    missing = client.get("/api/v1/catalog/estimate?items=HOOK-A:1&items=GONE-1:1")
    assert missing.status_code == 404 and "GONE-1" in missing.json()["detail"]


def test_an_order_line_takes_the_catalogs_filament_and_price(client):
    db = client.app.state.db
    db.rows["SELECT sku, name, material"] = [{**PRODUCT, "color": "white"}]
    r = client.post("/api/v1/commands/orders.create", headers={**csrf(client), "Idempotency-Key": KEY}, json={
        "customer_name": "Noa", "items": [{"product": "catalog", "sku": "HOOK-A", "quantity": 2, "niche": "kids"},
                                          {"product": "nameplate", "text": "Noa", "niche": "kids"}]})
    assert r.status_code == 202
    subject, published = client.app.state.bus.js.published[-1]
    line = published["data"]["items"][0]
    assert (line["unit_price_agorot"], line["color"], line["text"]) == (2500, "white", "Wall hook")
    # the niche comes from the catalog (a snapshot), never from the browser
    assert line["niche"] == "home" and published["data"]["items"][1]["niche"] == ""
    assert subject == "cmd.coordinator.create_order" and published["data"]["dispatch"] is False
    db.rows["SELECT sku, name, material"] = [{**PRODUCT, "active": False}]
    off = client.post("/api/v1/commands/orders.create", headers=csrf(client), json={
        "customer_name": "Noa", "items": [{"product": "catalog", "sku": "HOOK-A"}]})
    assert off.status_code == 400 and "switched off" in off.json()["detail"]


def test_catalog_commands():
    env = build_command("catalog.batch", {"items": [{"product": "catalog", "sku": "HOOK-A", "quantity": 5,
                                                     "unit_price_agorot": 2500}]}, "admin", KEY)
    assert env.data["order_id"] == f"stk-{KEY[:8]}" and env.data["channel"] == "stock" and env.data["dispatch"]
    assert env.data["items"][0]["unit_price_agorot"] == 0 and env.data["created_by"] == "admin"
    with pytest.raises(ValueError, match="catalog SKUs only"):
        build_command("catalog.batch", {"items": [{"text": "Noa"}]}, "admin", KEY)
    # a client cannot skip the gates of a normal order
    env = build_command("orders.create", {"customer_name": "Noa", "dispatch": True, "items": [{"text": "Noa"}]}, "admin", KEY)
    assert env.data["dispatch"] is False
    env = build_command("catalog.upsert", {"sku": "HOOK-A", "name": "Hook", "price_agorot": 2500, "upload": "a" * 64,
                                           "changed_by": "mallory"}, "admin", KEY)
    assert env.data["changed_by"] == "admin" and "upsert_product" in env.type
    env = build_command("catalog.slice", {"sku": "HOOK-A"}, "admin", KEY)
    assert env.data["requested_by"] == "admin"


# ---------------------------------------------------------------- images, switching on and off, deleting

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SHA = "ab" * 32
REFS = "count(DISTINCT order_id)"                     # a substring of REFERENCES_SQL only


def test_a_product_carries_its_image_url_and_the_image_is_served_as_what_it_is(client):
    db = client.app.state.db
    db.rows["FROM catalog.products p"] = [{**PRODUCT, "sold_7d": 0, "sold_prev_7d": 0, "in_production": 0,
                                           "image_sha": SHA, "image_source": "3mf"},
                                          {**PRODUCT, "sku": "BARE-1", "sold_7d": 0, "sold_prev_7d": 0, "in_production": 0,
                                           "image_sha": None, "image_source": None}]
    hook, bare = client.get("/api/v1/catalog/products").json()["products"]
    assert hook["image_url"] == f"/api/v1/catalog/products/HOOK-A/image?v={SHA[:16]}" and hook["image_source"] == "3mf"
    assert bare["image_url"] is None
    db.rows["FROM catalog.product_images WHERE sku"] = [{"sha256": SHA, "mime": "image/png", "data": PNG}]
    r = client.get("/api/v1/catalog/products/HOOK-A/image")
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and "immutable" in r.headers["cache-control"]
    # bytes that are no picture are never served, whatever type was stored
    db.rows["FROM catalog.product_images WHERE sku"] = [{"sha256": SHA, "mime": "image/png", "data": b"<svg/>"}]
    assert client.get("/api/v1/catalog/products/HOOK-A/image").status_code == 404
    db.rows["FROM catalog.product_images WHERE sku"] = []
    assert client.get("/api/v1/catalog/products/HOOK-A/image").status_code == 404
    assert client.get("/api/v1/catalog/products/bad sku/image").status_code == 400


def test_a_picture_upload_takes_png_jpeg_webp_only(client):
    r = client.post("/api/v1/catalog/images", content=PNG, headers=csrf(client))
    assert r.status_code == 201 and r.json()["type"] == "image/png"
    assert client.app.state.bus.kv["obs:uploads"][r.json()["image_upload"]] == PNG
    svg = client.post("/api/v1/catalog/images", content=b"<svg onload=alert(1)/>", headers=csrf(client))
    assert svg.status_code == 415
    big = client.post("/api/v1/catalog/images", content=PNG + b"\x00" * (2 * 1024 * 1024), headers=csrf(client))
    assert big.status_code == 413
    assert client.post("/api/v1/catalog/images", content=b"", headers=csrf(client)).status_code == 400
    assert client.post("/api/v1/catalog/images", content=PNG).status_code == 403                 # CSRF


def test_delete_answers_at_once_when_orders_keep_the_product(client):
    db = client.app.state.db
    db.rows["SELECT 1 FROM catalog.products WHERE sku"] = [{"?column?": 1}]
    db.rows[REFS] = [{"orders": 2, "stock": 0, "store_maps": 0}]
    refs = client.get("/api/v1/catalog/products/HOOK-A/references").json()
    assert refs["orders"] == 2 and "switch it off instead" in refs["blocked"]
    r = client.post("/api/v1/commands/catalog.delete", headers={**csrf(client), "Idempotency-Key": KEY}, json={"sku": "HOOK-A"})
    assert r.status_code == 409 and "2 orders" in r.json()["detail"]
    assert not client.app.state.bus.js.published                                                  # nothing reached the bus
    db.rows[REFS] = [{"orders": 0, "stock": 0, "store_maps": 0}]
    assert client.get("/api/v1/catalog/products/HOOK-A/references").json()["blocked"] is None
    ok = client.post("/api/v1/commands/catalog.delete", headers={**csrf(client), "Idempotency-Key": KEY}, json={"sku": "HOOK-A"})
    assert ok.status_code == 202
    subject, published = client.app.state.bus.js.published[-1]
    assert subject == "cmd.personalization.delete_product" and published["data"]["changed_by"] == "admin"
    db.rows["SELECT 1 FROM catalog.products WHERE sku"] = []
    gone = client.post("/api/v1/commands/catalog.delete", headers=csrf(client), json={"sku": "HOOK-A"})
    assert gone.status_code == 404
    assert client.get("/api/v1/catalog/products/HOOK-A/references").status_code == 404


def test_deleting_is_for_owners_switching_on_and_off_for_operators(client):
    import asyncio
    import importlib

    commands = importlib.import_module("app.commands")
    from gateway_support import PASSWORD, add_user

    assert commands.COMMANDS["catalog.delete"].min_role == "owner"
    assert commands.COMMANDS["catalog.active"].min_role == "operator"
    env = build_command("catalog.active", {"sku": "HOOK-A", "active": True, "changed_by": "mallory"}, "noa", KEY)
    assert env.data == {"sku": "HOOK-A", "active": True, "rights_confirmed": False, "changed_by": "noa"}
    assert "set_product_active" in env.type
    asyncio.run(add_user(client.app, "noa", "operator"))
    client.post("/api/v1/auth/login", json={"username": "noa", "password": PASSWORD})
    r = client.post("/api/v1/commands/catalog.delete", headers=csrf(client), json={"sku": "HOOK-A"})
    assert r.status_code == 403
    r = client.post("/api/v1/commands/catalog.active", headers={**csrf(client), "Idempotency-Key": KEY},
                    json={"sku": "HOOK-A", "active": False})
    assert r.status_code == 202
    audit = client.app.state.db.audit[KEY]
    assert audit["actor"] == "noa" and audit["subject"] == "cmd.personalization.set_product_active"


# ---------------------------------------------------------------- Import from link

def test_a_makerworld_link_is_recognised_and_never_fetched(client):
    r = client.get("/api/v1/catalog/link", params={"url": "https://makerworld.com/en/models/111937-pokemon-stand?x=1"})
    body = r.json()
    assert r.status_code == 200 and body["site"] == "makerworld" and body["model_id"] == "111937"
    assert body["url"] == "https://makerworld.com/en/models/111937" and body["lookup"] is None
    assert "no public API" in body["note"] and "3MF" in body["note"]
    assert client.app.state.bus.asked == []                               # nothing asked, nothing fetched
    bad = client.get("/api/v1/catalog/link", params={"url": "https://evil.example/models/1"})
    assert bad.status_code == 400 and "MakerWorld, Printables or Thingiverse" in bad.json()["detail"]


def test_a_thingiverse_link_asks_the_integrations_agent(client):
    bus = client.app.state.bus
    bus.replies["qry.integrations.model_lookup"] = {
        "found": True, "mode": "live", "title": "Pikachu card stand", "designer": "maker", "licence": "Creative Commons - Attribution",
        "image_url": "https://cdn.example/1.jpg", "files": ["stand.3mf"], "has_3mf": True, "reason": ""}
    body = client.get("/api/v1/catalog/link", params={"url": "thingiverse.com/thing:4321"}).json()
    assert bus.asked[0][0] == "qry.integrations.model_lookup" and bus.asked[0][1] == {"site": "thingiverse", "model_id": "4321"}
    assert body["lookup"]["licence_status"] == "commercial" and body["lookup"]["trademarks"] == ["pikachu"]
    assert body["lookup"]["has_3mf"] is True and body["url"] == "https://www.thingiverse.com/thing:4321"
    bus.replies["qry.integrations.model_lookup"] = {"found": False, "reason": "switch on the Thingiverse connector"}
    off = client.get("/api/v1/catalog/link", params={"url": "https://www.thingiverse.com/thing:4321"}).json()
    assert off["lookup"] is None and "switch on" in off["note"]
    bus.replies["qry.integrations.model_lookup"] = TimeoutError()
    gone = client.get("/api/v1/catalog/link", params={"url": "https://www.thingiverse.com/thing:4321"}).json()
    assert gone["lookup"] is None and "did not answer" in gone["note"]


def test_products_say_their_licence_and_brand_words(client):
    db = client.app.state.db
    db.rows["FROM catalog.products p"] = [{**PRODUCT, "name": "Charizard slab stand", "sold_7d": 0, "sold_prev_7d": 0,
                                           "in_production": 0, "licence": "BY-NC", "designer": "MaajkiPL",
                                           "source_url": "https://makerworld.com/en/models/1", "source_site": "makerworld"}]
    p = client.get("/api/v1/catalog/products").json()["products"][0]
    assert p["licence_status"] == "non_commercial" and p["licence_label"].startswith("CC BY-NC")
    assert p["trademarks"] == ["charizard"] and p["source_site"] == "makerworld"


def test_an_upsert_link_is_checked_and_made_canonical():
    env = build_command("catalog.upsert", {"sku": "HOOK-A", "name": "Hook", "price_agorot": 2500,
                                           "source_url": "makerworld.com/en/models/42-hook?from=x"}, "admin", KEY)
    assert env.data["source_url"] == "https://makerworld.com/en/models/42"
    with pytest.raises(ValueError, match="MakerWorld, Printables or Thingiverse"):
        build_command("catalog.upsert", {"sku": "HOOK-A", "name": "Hook", "price_agorot": 2500,
                                         "source_url": "https://evil.example/x"}, "admin", KEY)
