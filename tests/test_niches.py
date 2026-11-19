"""Wave 1 / M3: niches. The income split (pure), the niche snapshot on order lines, the niche commands and who may
send them, and the ``?niche=`` filter on the read side (Postgres faked)."""

from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

from printfarm.agents.finance.ledger import split_income
from printfarm.contracts.catalog import NAMEPLATE_NICHE, UNASSIGNED, valid_niche
from printfarm.contracts.messages import (
    ArchiveNiche,
    PaymentLine,
    PaymentRecorded,
    RecordQc,
    UpsertCatalogProduct,
    UpsertNiche,
)
from printfarm.contracts.orders import OrderLine, line_niche

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import KEY, csrf, gateway_client, login  # noqa: E402

commands = importlib.import_module("app.commands")
orders_mod = importlib.import_module("app.orders")


def lines(*weights: int, niche: str = "home") -> list[PaymentLine]:
    return [PaymentLine(product_type=f"SKU-{i}", niche=niche, weight_agorot=w) for i, w in enumerate(weights, start=1)]


# ---------------------------------------------------------------- the income split (pure)


def test_a_payment_splits_across_its_lines_and_adds_up_exactly():
    out = split_income(10000, lines(4500, 4500, 1000))
    assert [x.net_agorot for x in out] == [4500, 4500, 1000]
    odd = split_income(1001, lines(1, 1, 1))
    assert [x.net_agorot for x in odd] == [334, 334, 333] and sum(x.net_agorot for x in odd) == 1001
    # the remainder goes to the last line
    three = split_income(100, lines(1, 1, 1))
    assert [x.net_agorot for x in three] == [33, 33, 34]


def test_a_refund_splits_the_same_way_negative():
    pay, refund = split_income(8475, lines(5000, 2500)), split_income(-8475, lines(5000, 2500))
    assert [x.net_agorot for x in refund] == [-x.net_agorot for x in pay]
    assert sum(x.net_agorot for x in refund) == -8475


def test_free_lines_split_evenly_and_income_without_an_order_is_unassigned():
    assert [x.net_agorot for x in split_income(900, lines(0, 0, 0))] == [300, 300, 300]
    alone = split_income(5000, [], product_type="market")
    assert [(x.product_type, x.niche, x.net_agorot) for x in alone] == [("market", UNASSIGNED, 5000)]
    assert split_income(5000, [])[0].product_type == "other"


def test_split_keeps_each_lines_niche():
    mixed = [PaymentLine(product_type="nameplate", niche="gifts", weight_agorot=4500),
             PaymentLine(product_type="HOOK-A", niche="home", weight_agorot=2500)]
    assert [(x.niche, x.net_agorot) for x in split_income(7000, mixed)] == [("gifts", 4500), ("home", 2500)]


def test_payment_events_carry_their_lines_and_old_ones_still_parse():
    old = PaymentRecorded.model_validate({"payment_id": "p", "amount_agorot": 100, "method": "cash",
                                          "paid_at": "2026-11-01T10:00:00Z"})
    assert old.lines == []


# ---------------------------------------------------------------- the niche snapshot on order lines


def test_each_line_is_sold_under_a_niche():
    assert line_niche(OrderLine(product="nameplate", text="Noa")) == NAMEPLATE_NICHE
    assert line_niche(OrderLine(product="catalog", sku="HOOK-A", unit_price_agorot=100, niche="home")) == "home"
    assert line_niche(OrderLine(product="catalog", sku="HOOK-A", unit_price_agorot=100)) == UNASSIGNED
    with pytest.raises(ValidationError):
        OrderLine(product="nameplate", text="x", niche="Not A Slug")


def test_slugs():
    assert valid_niche("kids") and valid_niche("home-2") and not valid_niche("Kids") and not valid_niche("-x")


# ---------------------------------------------------------------- commands


def test_niche_commands_and_who_may_send_them():
    assert commands.COMMANDS["catalog.niche.upsert"].min_role == "owner"
    assert commands.COMMANDS["catalog.niche.archive"].min_role == "owner"
    assert commands.COMMANDS["catalog.niche.assign"].min_role == "operator"      # tidying the catalog is daily work
    env = commands.build_command("catalog.niche.upsert", {"slug": "pets", "name": "Pets", "color": 5,
                                                          "qc_checklist": ["No sharp edges"], "target_margin_pct": 55},
                                 "admin", KEY)
    cmd = env.payload()
    assert cmd.changed_by == "admin" and env.nats_subject() == "cmd.personalization.upsert_niche"
    for bad in ({"slug": "Pets", "name": "Pets"}, {"slug": "pets", "name": "Pets", "color": 9},
                {"slug": "pets", "name": "Pets", "qc_checklist": ["  "]},
                {"slug": "pets", "name": "Pets", "target_margin_pct": 100}):
        with pytest.raises(ValidationError):
            UpsertNiche.model_validate(bad)
    assert ArchiveNiche(slug="pets").move_to == UNASSIGNED


def test_a_product_takes_a_main_niche_and_tags():
    base = {"sku": "HOOK-A", "name": "Hook", "price_agorot": 100}
    assert UpsertCatalogProduct.model_validate(base).niche is None              # an edit keeps the niche
    p = UpsertCatalogProduct.model_validate({**base, "niche": "home", "tags": ["gifts"]})
    assert (p.niche, p.tags) == ("home", ["gifts"])
    with pytest.raises(ValidationError):
        UpsertCatalogProduct.model_validate({**base, "tags": ["Bad Tag"]})


def test_qc_carries_the_ticked_checklist():
    qc = RecordQc.model_validate({"order_id": "o1", "passed": True, "checklist": [{"item": "No stringing", "ok": True}]})
    assert qc.checklist[0].ok is True
    with pytest.raises(ValidationError):
        RecordQc.model_validate({"order_id": "o1", "passed": True, "checklist": [{"item": "", "ok": True}]})


def test_an_order_checklist_merges_its_niches_once():
    merged = orders_mod.qc_checklist([
        {"slug": "gifts", "name": "Gifts", "qc_checklist": ["Text spelled exactly", "No stringing"]},
        {"slug": "home", "name": "Home", "qc_checklist": ["no stringing", "Stands flat"]},
    ])
    assert [(x["item"], x["niche"]) for x in merged] == [("Text spelled exactly", "gifts"), ("No stringing", "gifts"),
                                                         ("Stands flat", "home")]


# ---------------------------------------------------------------- the ?niche= filter


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def recording(db):
    seen: list[tuple[str, tuple]] = []
    original = db.fetchall

    async def fetchall(sql, params=()):
        seen.append((sql, tuple(params)))
        return await original(sql, params)

    db.fetchall = fetchall
    return seen


def test_niches_list(client):
    db = client.app.state.db
    db.rows["FROM catalog.niches n"] = [{"slug": "home", "name": "Home décor", "color": 1, "products": 3, "tagged": 1}]
    r = client.get("/api/v1/niches")
    assert r.status_code == 200 and r.json()["niches"][0]["name"] == "Home décor"
    assert client.get("/api/v1/niches/Bad!").status_code == 400


@pytest.mark.parametrize("path", ["/api/v1/catalog/products", "/api/v1/orders", "/api/v1/orders/stats",
                                  "/api/v1/social/leads", "/api/v1/finance/products", "/api/v1/finance/report"])
def test_a_bad_niche_is_refused_and_all_means_no_filter(client, path):
    if path == "/api/v1/finance/report":
        assert client.get(f"{path}?niche=kids").status_code == 200     # the P&L is the whole business
        return
    assert client.get(f"{path}?niche=NOPE!").status_code == 400
    assert client.get(f"{path}?niche=all").status_code == 200


def test_the_catalog_filters_on_main_niche_or_tag(client):
    seen = recording(client.app.state.db)
    assert client.get("/api/v1/catalog/products?niche=kids").status_code == 200
    sql, params = seen[0]
    assert "p.niche = %s OR EXISTS" in sql and "catalog.product_tags" in sql and params.count("kids") == 2


def test_orders_and_leads_filter_on_the_niche(client):
    seen = recording(client.app.state.db)
    client.get("/api/v1/orders?niche=gifts")
    client.get("/api/v1/social/leads?niche=gifts&status=all")
    order_sql, order_params = next(s for s in seen if "coordinator.orders o" in s[0])
    assert "n.niche = %s" in order_sql and "gifts" in order_params
    lead_sql, lead_params = next(s for s in seen if "social.leads" in s[0])
    assert "coalesce(p.niche, 'unassigned') = %s" in lead_sql and lead_params[0] == "gifts"


def test_only_an_owner_changes_niches(client):
    ok = client.post("/api/v1/commands/catalog.niche.upsert", headers={**csrf(client), "Idempotency-Key": KEY},
                     json={"slug": "pets", "name": "Pets"})
    assert ok.status_code == 202
    subject, published = client.app.state.bus.js.published[-1]
    assert subject == "cmd.personalization.upsert_niche" and published["data"]["changed_by"] == "admin"
