"""Order saga v2 (pure): gates, auto-advance switches, multi-item orders, retries, QC, shipping and cancelling."""

from __future__ import annotations

import pytest

from printfarm.agents.coordinator.saga import (
    ALL_AUTO,
    MAX_QC_ROUNDS,
    State,
    Switches,
    decide,
    job_id,
    start_created,
    start_legacy,
)
from printfarm.contracts.messages import (
    AdvanceOrder,
    CancelOrder,
    CancelOrderJobs,
    CreateOrder,
    EnqueueJob,
    GenerateNameplate,
    JobCompleted,
    JobFailed,
    JobStarted,
    ModelFailed,
    ModelReady,
    OrderCancelled,
    OrderCompleted,
    OrderMoveRejected,
    OrderNeedsAttention,
    OrderQcRecorded,
    OrderReceived,
    OrderShipped,
    RecordQc,
    ShipOrder,
)
from printfarm.contracts.orders import OrderLine, price_order

OFF = Switches()


def order(as_lead=False, **kw) -> CreateOrder:
    return CreateOrder(order_id="o1", customer_name="Noa", channel="whatsapp", as_lead=as_lead, created_by="admin",
                       items=kw.pop("items", [OrderLine(text="Noa", quantity=2, unit_price_agorot=5000),
                                              OrderLine(text="Dan", color="white")]), **kw)


def ready(item: str) -> ModelReady:
    return ModelReady(order_id="o1", item_id=item, artifact_uri=f"file:///a/{item}.stl", source_uri="file:///a/x.scad",
                      footprint_mm=(120, 35), est_grams=20, est_minutes=40, sliced=False)


def step(d, msg, sw=OFF):
    return decide(d.state, d.context, msg, sw)


def kinds(d):
    return [type(m) for m in d.outgoing]


def to_qc(sw=OFF):
    d = start_created(order(), sw)
    d = step(d, AdvanceOrder(order_id="o1", to_state="PERSONALIZING", requested_by="admin"), sw)
    d = step(step(d, ready("1"), sw), ready("2"), sw)
    d = step(d, AdvanceOrder(order_id="o1", to_state="SCHEDULING", requested_by="admin"), sw)
    for unit in ("1.1", "1.2", "2.1"):
        i, u = unit.split(".")
        d = step(d, JobStarted(job_id=job_id("o1", i, u, 1), order_id="o1", plate_id="p", printer_id="x"), sw)
        d = step(d, JobCompleted(job_id=job_id("o1", i, u, 1), order_id="o1", plate_id="p"), sw)
    return d


def test_pricing_lines_discount_shipping():
    p = price_order([OrderLine(text="A", quantity=3, unit_price_agorot=4000), OrderLine(text="B")],
                    discount_agorot=1000, shipping_agorot=2500)
    assert p.lines == (12000, 4500) and p.subtotal == 16500 and p.total == 18000 and p.units == 4
    with pytest.raises(ValueError, match="larger than"):
        price_order([OrderLine(text="A")], discount_agorot=999999)
    with pytest.raises(ValueError, match="needs its text"):
        OrderLine(text=" ")
    with pytest.raises(ValueError, match="unknown channel"):
        CreateOrder(order_id="o1", customer_name="x", channel="fax", items=[OrderLine(text="a")])


def test_a_new_order_waits_at_every_gate_when_the_switches_are_off():
    d = start_created(order(), OFF)
    assert d.state == State.RECEIVED and d.outgoing == []
    assert d.context["order"]["total_agorot"] == 2 * 5000 + 4500
    assert set(d.context["units"]) == {"1.1", "1.2", "2.1"}

    d = step(d, AdvanceOrder(order_id="o1", to_state="PERSONALIZING", requested_by="admin"))
    assert d.state == State.PERSONALIZING and kinds(d) == [GenerateNameplate, GenerateNameplate]
    assert [m.item_id for m in d.outgoing] == ["1", "2"] and d.by == "admin"

    d = step(d, ready("1"))
    rejected = step(d, AdvanceOrder(order_id="o1", to_state="SCHEDULING", requested_by="admin"))
    assert rejected.state == State.PERSONALIZING and isinstance(rejected.outgoing[0], OrderMoveRejected)
    assert rejected.outgoing[0].reason == "model of item 2 is not ready yet"

    d = step(d, ready("2"))
    assert d.state == State.PERSONALIZING and d.outgoing == []      # ready, waiting for the dispatch
    d = step(d, AdvanceOrder(order_id="o1", to_state="SCHEDULING", requested_by="admin"))
    assert d.state == State.SCHEDULING and kinds(d) == [EnqueueJob] * 3
    assert {m.job_id for m in d.outgoing} == {"o1-i1-u1-a1", "o1-i1-u2-a1", "o1-i2-u1-a1"}
    assert d.outgoing[2].color == "white"


def test_switches_on_restore_the_automatic_flow():
    d = start_created(order(), ALL_AUTO)
    assert d.state == State.PERSONALIZING and kinds(d) == [GenerateNameplate] * 2
    d = step(step(d, ready("1"), ALL_AUTO), ready("2"), ALL_AUTO)
    assert d.state == State.SCHEDULING and kinds(d) == [EnqueueJob] * 3
    d = to_qc(ALL_AUTO)
    assert d.state == State.QC and d.context["qc"]["status"] == "passed"
    assert kinds(d) == [OrderQcRecorded] and d.outgoing[0].by == "auto"


def test_lead_must_be_confirmed_and_printing_cannot_be_dragged_into():
    d = start_created(order(as_lead=True), ALL_AUTO)
    assert d.state == State.LEAD and d.outgoing == []
    no = step(d, AdvanceOrder(order_id="o1", to_state="PRINTING", requested_by="admin"))
    assert no.state == State.LEAD and no.outgoing[0].reason == "printing starts when a printer takes the plate"
    d = step(d, AdvanceOrder(order_id="o1", to_state="RECEIVED", requested_by="admin"), ALL_AUTO)
    assert d.state == State.PERSONALIZING   # confirmed, and the switch dispatched personalization


def test_printing_then_qc_pass_then_ship():
    d = to_qc()
    assert d.state == State.QC and d.context["qc"]["status"] == "pending"
    no = step(d, ShipOrder(order_id="o1", shipped_by="admin"))
    assert isinstance(no.outgoing[0], OrderMoveRejected) and no.state == State.QC
    d = step(d, RecordQc(order_id="o1", passed=True, inspected_by="admin"))
    assert d.context["qc"]["status"] == "passed" and kinds(d) == [OrderQcRecorded]
    d = step(d, ShipOrder(order_id="o1", carrier="Israel Post", tracking="RR123", shipped_by="admin"))
    assert d.state == State.SHIPPED and kinds(d) == [OrderShipped, OrderCompleted]
    assert d.outgoing[0].total_agorot == 14500
    late = step(d, CancelOrder(order_id="o1", cancelled_by="admin"))
    assert late.state == State.SHIPPED and late.outgoing[0].reason == "the order is shipped"


def test_qc_failure_reprints_the_failed_units_and_too_many_rounds_need_a_person():
    d = to_qc()
    d = step(d, RecordQc(order_id="o1", passed=False, failed_units=["1.2"], reason="stringing", inspected_by="admin"))
    assert d.state == State.SCHEDULING and kinds(d) == [OrderQcRecorded, EnqueueJob]
    assert d.outgoing[1].job_id == "o1-i1-u2-a2" and d.context["units"]["1.1"]["status"] == "printed"
    d = step(d, JobStarted(job_id="o1-i1-u2-a2", order_id="o1", plate_id="p2", printer_id="x"))
    d = step(d, JobCompleted(job_id="o1-i1-u2-a2", order_id="o1", plate_id="p2"))
    assert d.state == State.QC
    for _ in range(MAX_QC_ROUNDS - 1):
        d = step(d, RecordQc(order_id="o1", passed=False, reason="warped", inspected_by="admin"))
        if d.state == State.NEEDS_ATTENTION:
            break
        for k, u in d.context["units"].items():
            i, n = k.split(".")
            d = step(d, JobCompleted(job_id=job_id("o1", i, n, u["attempt"]), order_id="o1", plate_id="p"))
    assert d.state == State.NEEDS_ATTENTION and d.context["attention"]["from"] == "QC"
    d = step(d, AdvanceOrder(order_id="o1", to_state="SCHEDULING", requested_by="admin"))
    assert d.state == State.SCHEDULING and len(d.outgoing) == 3 and d.context["qc"]["rounds"] == 0


def test_failed_print_is_retried_then_needs_a_person_then_retried_by_hand():
    d = start_created(order(items=[OrderLine(text="A")]), ALL_AUTO)
    d = step(d, ready("1"), ALL_AUTO)
    d = step(d, JobFailed(job_id="o1-i1-u1-a1", order_id="o1", plate_id="p", reason="spaghetti"), ALL_AUTO)
    assert d.state == State.SCHEDULING and d.outgoing[0].job_id == "o1-i1-u1-a2"
    stale = step(d, JobFailed(job_id="o1-i1-u1-a1", order_id="o1", plate_id="p", reason="dup"), ALL_AUTO)
    assert not stale.changed
    d = step(d, JobFailed(job_id="o1-i1-u1-a2", order_id="o1", plate_id="p", reason="spaghetti"), ALL_AUTO)
    assert d.state == State.NEEDS_ATTENTION and kinds(d) == [OrderNeedsAttention]
    d = step(d, AdvanceOrder(order_id="o1", to_state="SCHEDULING", requested_by="admin"))
    assert d.state == State.SCHEDULING and d.outgoing[0].job_id == "o1-i1-u1-a3"


def test_failed_nameplate_is_retried_with_a_new_text():
    d = start_created(order(), ALL_AUTO)
    d = step(d, ModelFailed(order_id="o1", item_id="2", reason="text wider than the plate"), ALL_AUTO)
    assert d.state == State.NEEDS_ATTENTION and "item 2" in d.context["attention"]["reason"]
    d = step(d, ready("1"), ALL_AUTO)            # the other item still finishes
    assert d.state == State.NEEDS_ATTENTION and d.context["items"]["1"]["model_status"] == "ready"
    bad = step(d, AdvanceOrder(order_id="o1", to_state="PERSONALIZING", texts={"2": ""}, requested_by="admin"))
    assert bad.outgoing[0].reason == "item 2: the text must be 1-40 characters"
    d = step(d, AdvanceOrder(order_id="o1", to_state="PERSONALIZING", texts={"2": "D."}, requested_by="admin"), ALL_AUTO)
    assert d.state == State.PERSONALIZING and [(m.item_id, m.text) for m in d.outgoing] == [("2", "D.")]
    d = step(d, ready("2"), ALL_AUTO)
    assert d.state == State.SCHEDULING


def test_cancel_before_printing_drops_queued_jobs_but_not_once_a_printer_has_a_unit():
    d = start_created(order(items=[OrderLine(text="A")]), ALL_AUTO)
    d = step(d, ready("1"), ALL_AUTO)
    c = step(d, CancelOrder(order_id="o1", reason="customer changed mind", cancelled_by="admin"))
    assert c.state == State.CANCELLED and kinds(c) == [OrderCancelled, CancelOrderJobs]
    d = step(d, JobStarted(job_id="o1-i1-u1-a1", order_id="o1", plate_id="p", printer_id="x"))
    no = step(d, CancelOrder(order_id="o1", cancelled_by="admin"))
    assert no.state == State.PRINTING and no.outgoing[0].reason.startswith("units are on a printer")


def test_v1_orders_still_flow_with_the_switches_on():
    d = start_legacy(OrderReceived(order_id="o1", customer_ref="c1", text="Noa", quantity=2), ALL_AUTO)
    assert d.state == State.PERSONALIZING and d.outgoing[0].item_id == "1" and d.context["order"]["channel"] == "api"
    d = step(d, ModelReady(order_id="o1", artifact_uri="file:///a.stl", source_uri="file:///a.scad",
                           footprint_mm=(120, 35), est_grams=20, est_minutes=40, sliced=False), ALL_AUTO)
    assert d.state == State.SCHEDULING and {m.job_id for m in d.outgoing} == {"o1-i1-u1-a1", "o1-i1-u2-a1"}
    assert not step(d, GenerateNameplate(order_id="o1", text="x", material="PLA", color="black")).changed
