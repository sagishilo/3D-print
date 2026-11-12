from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from printfarm.contracts import REGISTRY, Envelope, Kind, deterministic_id, new_message, subject_matches
from printfarm.contracts.messages import EnqueueJob, ModelReady, OrderReceived, PrinterState, PrinterStatus


def test_envelope_roundtrip_and_payload():
    env = new_message(OrderReceived(order_id="o1", customer_ref="c", text="נועה"), source="/cli", correlationid="o1")
    back = Envelope.from_bytes(env.to_bytes())
    assert back == env
    assert back.type == "io.printfarm.order.order.received.v1"
    assert back.kind == Kind.EVENT
    assert back.nats_subject() == "evt.order.order.received"
    assert back.payload().text == "נועה"


def test_causation_and_correlation_flow():
    first = new_message(OrderReceived(order_id="o1", customer_ref="c", text="x"), source="/cli", correlationid="o1")
    second = new_message(
        ModelReady(order_id="o1", artifact_uri="file:///a", source_uri="file:///s", footprint_mm=(1, 2),
                   est_grams=1, est_minutes=1, sliced=False),
        source="/agents/personalization", cause=first,
    )
    assert second.correlationid == "o1"
    assert second.causationid == first.id


def test_telemetry_subject_is_partitioned_per_printer():
    s = PrinterState(printer_id="p1", vendor="bambu", status=PrinterStatus.IDLE, observed_at=datetime.now(UTC))
    env = new_message(s, source="/agents/telemetry", subject="p1")
    assert env.nats_subject() == "tlm.p1.state"


def test_commands_expire():
    env = new_message(
        EnqueueJob(job_id="j", order_id="o", artifact_uri="a", material="PLA", color="black", footprint_mm=(1, 1),
                   est_grams=1, est_minutes=1),
        source="/x", deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert env.kind == Kind.COMMAND and env.is_expired()


def test_payloads_reject_unknown_fields_and_bad_values():
    with pytest.raises(ValidationError):
        OrderReceived(order_id="o", customer_ref="c", text="x", bogus=1)
    with pytest.raises(ValidationError):
        OrderReceived(order_id="o", customer_ref="c", text="x" * 41)


def test_registry_prefixes():
    for t, model in REGISTRY.items():
        assert t.startswith("io.printfarm.")
        assert model.subject.split(".")[0] in {"evt", "cmd", "tlm", "qry", "dlq"}
        if model.subject.startswith("cmd."):
            assert model.kind == Kind.COMMAND


def test_deterministic_ids_are_stable():
    assert deterministic_id("p1", "plate", "finished") == deterministic_id("p1", "plate", "finished")
    assert deterministic_id("p1", "plate", "finished") != deterministic_id("p1", "plate", "started")


@pytest.mark.parametrize(
    "pattern,subject,ok",
    [
        ("evt.fleet.job.>", "evt.fleet.job.completed", True),
        ("evt.fleet.job.>", "evt.fleet.job", False),
        ("tlm.*.state", "tlm.p1.state", True),
        ("tlm.*.state", "tlm.p1.x.state", False),
        ("cmd.fleet.enqueue_job", "cmd.fleet.enqueue_job", True),
    ],
)
def test_subject_matching(pattern, subject, ok):
    assert subject_matches(pattern, subject) is ok
