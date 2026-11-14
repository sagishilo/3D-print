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
           "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z", "updated_by": "admin"}


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def test_products_with_their_demand(client):
    db = client.app.state.db
    db.rows["FROM catalog.products p"] = [{**PRODUCT, "sold_7d": 12, "sold_prev_7d": 8, "in_production": 4}]
    db.rows["FROM catalog.products WHERE active OR"] = [{"category": "home", "n": 1}]
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
        "customer_name": "Noa", "items": [{"product": "catalog", "sku": "HOOK-A", "quantity": 2}]})
    assert r.status_code == 202
    subject, published = client.app.state.bus.js.published[-1]
    line = published["data"]["items"][0]
    assert (line["unit_price_agorot"], line["color"], line["text"]) == (2500, "white", "Wall hook")
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
