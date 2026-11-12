from datetime import UTC, datetime, timedelta

from printfarm.bus import MemoryBus
from printfarm.contracts import new_message
from printfarm.contracts.messages import EnqueueJob, OrderReceived


def order(oid="o1"):
    return new_message(OrderReceived(order_id=oid, customer_ref="c", text="x"), source="/t", correlationid=oid)


async def test_failing_handler_is_retried_then_dead_lettered():
    bus = MemoryBus()
    calls = []

    async def boom(env):
        calls.append(env.id)
        raise RuntimeError("nope")

    await bus.subscribe("svc", "evt.order.>", boom, max_deliver=3)
    await bus.publish(order())
    await bus.drain()
    assert len(calls) == 3
    dead = [e for e in bus.published if e.nats_subject() == "dlq.message.dead_lettered"]
    assert len(dead) == 1 and dead[0].payload().consumer == "svc" and dead[0].correlationid == "o1"
    await bus.close()


async def test_duplicate_publish_is_dropped():
    bus = MemoryBus()
    seen = []

    async def h(env):
        seen.append(env.id)

    await bus.subscribe("svc", "evt.>", h)
    env = order()
    await bus.publish(env)
    await bus.publish(env)
    await bus.drain()
    assert seen == [env.id]
    await bus.close()


async def test_expired_command_is_not_executed():
    bus = MemoryBus()
    seen = []

    async def h(env):
        seen.append(env)

    await bus.subscribe("fleet", "cmd.fleet.>", h)
    cmd = new_message(
        EnqueueJob(job_id="j", order_id="o", artifact_uri="a", material="PLA", color="black", footprint_mm=(1, 1),
                   est_grams=1, est_minutes=1),
        source="/t", deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    await bus.publish(cmd)
    await bus.drain()
    assert seen == []
    await bus.close()
