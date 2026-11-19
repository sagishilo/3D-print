"""Wave 1 / M4: stock beyond filament. The pure rules (shelf taking, restock, bill of materials, item reorder,
filament drying), the saga with shelf pieces (an order from stock, partly from stock, stock running out), and the
gateway's read side and commands (Postgres faked)."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest

from printfarm.agents.coordinator.saga import ALL_AUTO, State, Switches, decide, start_created
from printfarm.contracts.messages import (
    CancelOrder,
    CreateOrder,
    EnqueueJob,
    JobCompleted,
    ModelReady,
    OrderQcRecorded,
    OrderShipped,
    PrepareCatalogItem,
    RecordQc,
    ShipOrder,
    StockMoved,
    UpsertItem,
)
from printfarm.contracts.orders import OrderLine
from printfarm.contracts.stock import (
    bom_use,
    days_open,
    dry_after_days,
    item_reorder_qty,
    needs_drying,
    restock_suggestion,
    take_from_stock,
)

OFF = Switches()

# ---------------------------------------------------------------- pure rules


def test_lines_take_from_the_shelf_first_come_first_served():
    assert take_from_stock([("HOOK", 2), ("", 1), ("HOOK", 3), ("BOWL", 1)], {"HOOK": 4}) == [2, 0, 2, 0]
    assert take_from_stock([("HOOK", 1)], {"HOOK": -2}) == [0]


def test_restock_counts_what_is_already_being_made():
    assert restock_suggestion(on_hand=3, reserved=1, min_stock=10, in_production=2) == 6
    assert restock_suggestion(on_hand=12, reserved=0, min_stock=10, in_production=0) == 0


def test_bom_parts_for_what_is_made_packaging_for_what_leaves():
    bom = {"HOOK": [("magnet", 2, "part"), ("box", 1, "packaging"), ("glue", 0.5, "consumable")]}
    # 3 sold, 1 of them from the shelf: parts for the 2 made now, a box for each of the 3
    uses = {(u.item_id, u.reason): u.qty for u in bom_use(bom, [("HOOK", 3, 1)], to_shelf=False)}
    assert uses == {("magnet", "made"): 4, ("glue", "made"): 1.0, ("box", "shipped"): 3}
    # a stock batch to the shelf: parts only, no box yet
    shelf = {(u.item_id, u.reason): u.qty for u in bom_use(bom, [("HOOK", 5, 0)], to_shelf=True)}
    assert shelf == {("magnet", "made"): 10, ("glue", "made"): 2.5}
    assert bom_use(bom, [("OTHER", 2, 0)], to_shelf=False) == []


def test_item_reorder():
    assert item_reorder_qty(on_hand=10, on_order=0, min_qty=50, reorder_qty=100) == 100
    assert item_reorder_qty(on_hand=10, on_order=0, min_qty=200, reorder_qty=100) == 190    # at least back to the minimum
    assert item_reorder_qty(on_hand=10, on_order=60, min_qty=50, reorder_qty=100) == 0      # enough on its way
    assert item_reorder_qty(on_hand=0, on_order=0, min_qty=0, reorder_qty=100) == 0         # no minimum: no rule


def test_filament_drying_clock():
    now = datetime(2026, 11, 19, tzinfo=UTC)
    assert dry_after_days("petg") == 21 and dry_after_days("Mystery") == 30
    assert days_open(None, None, now) is None
    opened = now - timedelta(days=25)
    assert needs_drying("PETG", opened, None, now) and not needs_drying("PLA", opened, None, now)
    assert not needs_drying("PETG", opened, now - timedelta(days=2), now)     # dried two days ago
    assert round(days_open(opened, None, now), 1) == 25.0


# ---------------------------------------------------------------- the saga with shelf pieces


def hooks(qty: int, *, as_lead=False, channel="whatsapp", dispatch=False, order_id="o1") -> CreateOrder:
    return CreateOrder(order_id=order_id, customer_name="Noa", channel=channel, created_by="admin", as_lead=as_lead,
                       dispatch=dispatch, items=[OrderLine(product="catalog", sku="HOOK", quantity=qty, unit_price_agorot=2500)])


def ready(item: str = "1", oid: str = "o1") -> ModelReady:
    return ModelReady(order_id=oid, item_id=item, artifact_uri="file:///c/HOOK.3mf", source_uri="file:///c/HOOK.3mf",
                      footprint_mm=(50, 50), est_grams=20, est_minutes=40, sliced=True)


def step(d, msg, sw=OFF):
    return decide(d.state, d.context, msg, sw)


def moves(d) -> list[tuple[str, int, int, str]]:
    return [(m.sku, m.delta_on_hand, m.delta_reserved, m.reason) for m in d.outgoing if isinstance(m, StockMoved)]


def test_an_order_all_from_the_shelf_goes_straight_to_qc_and_is_consumed_when_shipped():
    d = start_created(hooks(2), OFF, {"HOOK": 5})
    assert d.state == State.QC and moves(d) == [("HOOK", 0, 2, "reserved")]
    assert not any(isinstance(m, PrepareCatalogItem | EnqueueJob) for m in d.outgoing)
    assert {u["status"] for u in d.context["units"].values()} == {"stock"}
    d = step(d, RecordQc(order_id="o1", passed=True, inspected_by="noa"))
    d = step(d, ShipOrder(order_id="o1", shipped_by="noa"))
    assert d.state == State.SHIPPED and moves(d) == [("HOOK", -2, -2, "sold")]
    shipped = next(m for m in d.outgoing if isinstance(m, OrderShipped))
    assert [(x.product_type, x.quantity, x.from_stock) for x in shipped.lines] == [("HOOK", 2, 2)]


def test_auto_qc_passes_a_shelf_order_at_once():
    d = start_created(hooks(1), ALL_AUTO, {"HOOK": 1})
    assert d.state == State.QC and d.context["qc"]["status"] == "passed"
    assert any(isinstance(m, OrderQcRecorded) for m in d.outgoing)


def test_partly_from_the_shelf_prints_only_the_rest():
    d = start_created(hooks(3), ALL_AUTO, {"HOOK": 1})          # stock runs out after one piece
    assert d.state == State.PERSONALIZING and moves(d) == [("HOOK", 0, 1, "reserved")]
    d = step(d, ready(), ALL_AUTO)
    jobs = [m for m in d.outgoing if isinstance(m, EnqueueJob)]
    assert len(jobs) == 2 and d.context["units"]["1.1"]["status"] == "stock"
    for j in jobs:
        d = step(d, JobCompleted(order_id="o1", job_id=j.job_id, plate_id="p1"), ALL_AUTO)
    assert d.state == State.QC                                  # printed + shelf = every unit done
    d = step(d, ShipOrder(order_id="o1", shipped_by="noa"))
    assert moves(d) == [("HOOK", -1, -1, "sold")]


def test_no_shelf_no_reservation_and_a_lead_reserves_until_cancelled():
    d = start_created(hooks(2), OFF, {})
    assert d.state == State.RECEIVED and moves(d) == []
    lead = start_created(hooks(2, as_lead=True), OFF, {"HOOK": 9})
    assert lead.state == State.LEAD and moves(lead) == [("HOOK", 0, 2, "reserved")]
    gone = step(lead, CancelOrder(order_id="o1", reason="changed mind", cancelled_by="noa"))
    assert gone.state == State.CANCELLED and moves(gone) == [("HOOK", 0, -2, "released")]


def test_a_stock_batch_never_takes_from_the_shelf_and_builds_it_when_shipped():
    d = start_created(hooks(4, channel="stock", dispatch=True, order_id="stk-1"), OFF, {"HOOK": 10})
    assert moves(d) == []
    d = step(d, ready(oid="stk-1"))
    for j in [m for m in d.outgoing if isinstance(m, EnqueueJob)]:
        d = step(d, JobCompleted(order_id="stk-1", job_id=j.job_id, plate_id="p1"))
    d = step(d, RecordQc(order_id="stk-1", passed=True, inspected_by="noa"))
    d = step(d, ShipOrder(order_id="stk-1", shipped_by="noa"))
    assert moves(d) == [("HOOK", 4, 0, "built")]
    assert next(m for m in d.outgoing if isinstance(m, OrderShipped)).channel == "stock"


def test_a_shelf_piece_that_fails_qc_is_printed_new_after_its_model_is_prepared():
    d = start_created(hooks(1), OFF, {"HOOK": 1})
    d = step(d, RecordQc(order_id="o1", passed=False, failed_units=["1.1"], reason="cracked", inspected_by="noa"))
    assert d.state == State.PERSONALIZING
    assert any(isinstance(m, PrepareCatalogItem) for m in d.outgoing)
    d = step(d, ready(), Switches(schedule=True))
    assert [m.job_id for m in d.outgoing if isinstance(m, EnqueueJob)] == ["o1-i1-u1-a2"]
    # the shelf piece was used up even though it was reprinted
    d = step(d, JobCompleted(order_id="o1", job_id="o1-i1-u1-a2", plate_id="p"))
    d = step(d, RecordQc(order_id="o1", passed=True, inspected_by="noa"))
    d = step(d, ShipOrder(order_id="o1", shipped_by="noa"))
    assert moves(d) == [("HOOK", -1, -1, "sold")]


# ---------------------------------------------------------------- gateway (Postgres faked)

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import KEY, gateway_client, login  # noqa: E402

commands = importlib.import_module("app.commands")


@pytest.fixture
def client():
    with gateway_client() as c:
        login(c)
        yield c


def test_stock_commands_are_operator_work():
    for name in ("stock.rule", "stock.count", "items.upsert", "items.count", "items.reorder", "bom.set", "spools.dried"):
        assert commands.COMMANDS[name].min_role == "operator", name
    env = commands.build_command("bom.set", {"sku": "HOOK", "lines": [{"item_id": "magnet-6mm", "qty": 2}]}, "noa", KEY)
    assert env.nats_subject() == "cmd.inventory.set_bom" and env.payload().changed_by == "noa"
    assert commands.build_command("stock.count", {"sku": "HOOK", "on_hand": 4}, "noa", KEY).nats_subject() == \
        "cmd.coordinator.count_stock"
    with pytest.raises(ValueError):
        UpsertItem.model_validate({"item_id": "Bad Id", "name": "x"})


def test_finished_goods_value_free_and_restock(client):
    db = client.app.state.db
    db.rows["FROM catalog.products p LEFT JOIN coordinator.finished_goods f"] = [
        {"sku": "HOOK", "name": "Hook", "niche": "home", "active": True, "on_hand": 5, "reserved": 2, "min_stock": 6,
         "location": "B2", "updated_at": None, "in_production": 1, "built_units": 10, "built_cost_agorot": 3000},
        {"sku": "BOWL", "name": "Bowl", "niche": "home", "active": True, "on_hand": 0, "reserved": 0, "min_stock": 0,
         "location": "", "updated_at": None, "in_production": 0, "built_units": 0, "built_cost_agorot": 0},
    ]
    r = client.get("/api/v1/inventory/finished-goods").json()
    hook = next(x for x in r["items"] if x["sku"] == "HOOK")
    assert (hook["free"], hook["unit_cost_agorot"], hook["value_agorot"], hook["low"], hook["restock"]) == (3, 300, 1500, True, 2)
    assert r["value_agorot"] == 1500 and r["low"] == 1
    assert next(x for x in r["items"] if x["sku"] == "BOWL")["unit_cost_agorot"] is None


def test_items_suggest_a_reorder(client):
    client.app.state.db.rows["FROM inventory.items i"] = [
        {"item_id": "box-s", "name": "Gift box S", "kind": "packaging", "unit": "pcs", "on_hand": 4.0, "min_qty": 20.0,
         "reorder_qty": 50.0, "cost_agorot": 120, "on_order": 0.0, "used_by": [{"sku": "HOOK", "qty": 1}], "used_30d": 12.0,
         "archived": False}]
    item = client.get("/api/v1/inventory/items").json()["items"][0]
    assert item["low"] and item["suggested_qty"] == 50 and item["suggested_cost_agorot"] == 6000


def test_condition_lists_spools_due_for_drying(client):
    opened = datetime.now(UTC) - timedelta(days=30)
    client.app.state.db.rows["FROM inventory.spools WHERE status <> 'empty' AND opened_at IS NOT NULL"] = [
        {"spool_id": "S1", "material": "PETG", "color": "black", "brand": "", "printer_id": "x1", "slot": None,
         "location": None, "opened_at": opened, "dried_at": None, "remaining_grams": 500},
        {"spool_id": "S2", "material": "PLA", "color": "white", "brand": "", "printer_id": None, "slot": None,
         "location": "A1", "opened_at": opened, "dried_at": None, "remaining_grams": 800}]
    r = client.get("/api/v1/inventory/condition").json()
    assert r["due"] == 1 and [s["spool_id"] for s in r["spools"] if s["needs_drying"]] == ["S1"]
    assert r["humid_level"] == 4 and isinstance(r["humid"], list)
