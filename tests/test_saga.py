from printfarm.agents.coordinator import saga
from printfarm.agents.coordinator.saga import State
from printfarm.contracts.messages import (
    EnqueueJob,
    GenerateNameplate,
    JobCompleted,
    JobFailed,
    JobStarted,
    ModelFailed,
    ModelReady,
    OrderCompleted,
    OrderNeedsAttention,
    OrderReceived,
)

READY = ModelReady(order_id="o1", artifact_uri="file:///a.stl", source_uri="file:///a.scad", footprint_mm=(120, 35),
                   est_grams=10, est_minutes=20, sliced=False)


def started(qty=2):
    d = saga.start(OrderReceived(order_id="o1", customer_ref="c", text="Noa", quantity=qty))
    assert d.state == State.MODELING and isinstance(d.outgoing[0], GenerateNameplate)
    return d


def test_happy_path_two_units():
    d = started()
    d = saga.decide(d.state, d.context, READY)
    assert d.state == State.QUEUED
    jobs = [m.job_id for m in d.outgoing if isinstance(m, EnqueueJob)]
    assert jobs == ["o1-u1-a1", "o1-u2-a1"]

    d = saga.decide(d.state, d.context, JobStarted(job_id=jobs[0], order_id="o1", plate_id="p", printer_id="x"))
    assert d.state == State.PRINTING
    d = saga.decide(d.state, d.context, JobCompleted(job_id=jobs[0], order_id="o1", plate_id="p"))
    assert d.state == State.PRINTING and not d.outgoing
    d = saga.decide(d.state, d.context, JobCompleted(job_id=jobs[1], order_id="o1", plate_id="p"))
    assert d.state == State.COMPLETED and isinstance(d.outgoing[0], OrderCompleted)


def test_duplicate_completion_is_ignored():
    d = started(qty=1)
    d = saga.decide(d.state, d.context, READY)
    done = JobCompleted(job_id="o1-u1-a1", order_id="o1", plate_id="p")
    d = saga.decide(d.state, d.context, done)
    again = saga.decide(d.state, d.context, done)
    assert not again.changed


def test_failed_print_is_retried_then_escalated():
    d = started(qty=1)
    d = saga.decide(d.state, d.context, READY)
    d = saga.decide(d.state, d.context, JobFailed(job_id="o1-u1-a1", order_id="o1", plate_id="p", reason="spaghetti"))
    assert [m.job_id for m in d.outgoing] == ["o1-u1-a2"]
    # a late event from the superseded attempt changes nothing
    stale = saga.decide(d.state, d.context, JobCompleted(job_id="o1-u1-a1", order_id="o1", plate_id="p"))
    assert not stale.changed
    d = saga.decide(d.state, d.context, JobFailed(job_id="o1-u1-a2", order_id="o1", plate_id="p2", reason="again"))
    assert d.state == State.NEEDS_ATTENTION and isinstance(d.outgoing[0], OrderNeedsAttention)


def test_model_failure_needs_attention():
    d = started()
    d = saga.decide(d.state, d.context, ModelFailed(order_id="o1", reason="bad font"))
    assert d.state == State.NEEDS_ATTENTION


def test_decide_does_not_mutate_input_context():
    d = started(qty=1)
    d2 = saga.decide(d.state, d.context, READY)
    before = d2.context["units"]["1"]["status"]
    saga.decide(d2.state, d2.context, JobStarted(job_id="o1-u1-a1", order_id="o1", plate_id="p", printer_id="x"))
    assert d2.context["units"]["1"]["status"] == before
