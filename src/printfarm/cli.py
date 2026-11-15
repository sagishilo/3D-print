"""Command line entry point.

    printfarm migrate                       apply database migrations
    printfarm migrate --status|--check      what is applied / pending (no changes)
    printfarm agent <name> [<name> ...]     run agents (one process per agent in docker-compose)
    printfarm dev                           run every agent in one process
    printfarm sim                           run the simulated printers
    printfarm order --text "Noa" [--qty 2]  submit a nameplate order (acts as the storefront)
    printfarm approvals                     list pending human approvals
    printfarm approve <id> [--reject]       decide an approval
    printfarm clear-bed <printer>           operator confirms a bed is cleared
    printfarm status                        printers, open orders
    printfarm watch                         tail all events and commands
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import uuid
from datetime import UTC, datetime, timedelta

from printfarm.config import Settings

AGENTS = ("coordinator", "personalization", "fleet", "telemetry", "inventory", "finance", "social")


def agent_class(name: str):
    from printfarm.agents.coordinator.agent import CoordinatorAgent
    from printfarm.agents.finance.agent import FinanceAgent
    from printfarm.agents.fleet.agent import FleetAgent
    from printfarm.agents.inventory.agent import InventoryAgent
    from printfarm.agents.personalization.agent import PersonalizationAgent
    from printfarm.agents.social.agent import SocialAgent
    from printfarm.agents.telemetry.agent import TelemetryAgent

    return {
        "coordinator": CoordinatorAgent,
        "personalization": PersonalizationAgent,
        "fleet": FleetAgent,
        "telemetry": TelemetryAgent,
        "inventory": InventoryAgent,
        "finance": FinanceAgent,
        "social": SocialAgent,
    }[name]


async def _connect(settings: Settings, name: str = "printfarm-cli"):
    from printfarm.bus.nats_bus import NatsBus
    from printfarm.store import Database

    bus = await NatsBus.connect(settings.nats_url, name=name)  # the name shows up in the dashboard's NATS connections
    db = Database(settings.database_url)
    await db.open()
    return bus, db


async def run_agents(names: list[str], settings: Settings, stop: asyncio.Event | None = None) -> None:
    bus, db = await _connect(settings, name=f"agent:{'+'.join(names)}")
    await db.migrate()
    fleet = settings.fleet()
    agents = [agent_class(n)(bus, db, settings, fleet) for n in names]
    for a in agents:
        await a.start()
        logging.getLogger("printfarm").info("agent %s started", a.name)
    stop = stop or _stop_event()
    try:
        await stop.wait()
    finally:
        for a in agents:
            await a.stop()
        await bus.close()
        await db.close()


def _stop_event() -> asyncio.Event:
    ev = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, ev.set)
    return ev


async def _publish(settings: Settings, payload, **kw):
    from printfarm.bus.nats_bus import NatsBus
    from printfarm.contracts import new_message

    bus = await NatsBus.connect(settings.nats_url, name="printfarm-cli")
    env = new_message(payload, source="/cli", **kw)
    await bus.publish(env)
    await bus.close()
    return env


async def cmd_order(args, settings: Settings) -> None:
    from printfarm.contracts.messages import OrderReceived

    order_id = args.id or f"ord-{uuid.uuid4().hex[:8]}"
    due = datetime.now(UTC) + timedelta(days=args.due_days) if args.due_days else None
    await _publish(
        settings,
        OrderReceived(order_id=order_id, customer_ref=args.customer, text=args.text, material=args.material,
                      color=args.color, quantity=args.qty, due_by=due),
        correlationid=order_id, subject=order_id,
    )
    print(order_id)


async def cmd_approve(args, settings: Settings) -> None:
    from printfarm.contracts.messages import DecideApproval

    await _publish(settings, DecideApproval(approval_id=args.approval_id, approved=not args.reject,
                                            decided_by=args.by, note=args.note))
    print("rejected" if args.reject else "approved", args.approval_id)


async def cmd_clear_bed(args, settings: Settings) -> None:
    from printfarm.contracts.messages import MarkBedCleared

    await _publish(settings, MarkBedCleared(printer_id=args.printer_id, operator=args.by))
    print("bed cleared:", args.printer_id)


async def cmd_approvals(args, settings: Settings) -> None:
    from printfarm.store import Database

    db = Database(settings.database_url)
    await db.open()
    async with db.tx() as conn:
        rows = await (
            await conn.execute("SELECT approval_id, action, summary, created_at FROM coordinator.approvals "
                               "WHERE status = 'pending' ORDER BY created_at")
        ).fetchall()
    await db.close()
    for r in rows:
        print(f"{r['approval_id']}  {r['action']:<20} {r['summary']}  ({r['created_at']:%Y-%m-%d %H:%M})")
    if not rows:
        print("no pending approvals")


async def cmd_status(args, settings: Settings) -> None:
    from printfarm.contracts import Envelope

    bus, db = await _connect(settings)
    print("PRINTERS")
    for key in sorted(await bus.kv_keys("printer_status")):
        raw = await bus.kv_get("printer_status", key)
        s = Envelope.from_bytes(raw).data if raw else {}
        print(f"  {key:<12} {s.get('status', '?'):<9} {s.get('progress', 0) * 100:5.1f}%  "
              f"nozzle {s.get('nozzle_temp')}/{s.get('nozzle_target')}  plate {s.get('plate_id') or '-'}")
    async with db.tx() as conn:
        rows = await (
            await conn.execute("SELECT saga_id, state, updated_at FROM coordinator.sagas ORDER BY updated_at DESC LIMIT 15")
        ).fetchall()
    print("ORDERS")
    for r in rows:
        print(f"  {r['saga_id']:<16} {r['state']:<16} {r['updated_at']:%H:%M:%S}")
    await bus.close()
    await db.close()


async def cmd_watch(args, settings: Settings) -> None:
    import nats

    nc = await nats.connect(settings.nats_url)

    async def show(msg):
        try:
            env = json.loads(msg.data)
            print(f"{env['time'][11:19]}  {msg.subject:<42} corr={env['correlationid'][:16]:<16} "
                  f"{json.dumps(env['data'], ensure_ascii=False)[:120]}", flush=True)
        except Exception:  # noqa: BLE001
            print(msg.subject, msg.data[:200])

    for subj in ("evt.>", "cmd.>", "dlq.>") + (("tlm.>",) if args.telemetry else ()):
        await nc.subscribe(subj, cb=show)
    await _stop_event().wait()
    await nc.drain()


async def cmd_migrate(args, settings: Settings) -> None:
    from printfarm.store import Database
    from printfarm.store.schema import MigrationError

    db = Database(settings.database_url)
    await db.open()
    try:
        if args.status or args.check:
            plan = await db.migration_plan()
            print(f"applied: {plan.applied} (latest {plan.latest or '-'})")
            for m in plan.pending:
                print(f"pending: {m.name}")
            for problem in plan.problems:
                print(f"ERROR:   {problem}")
            if args.check and (plan.pending or not plan.ok):
                raise SystemExit(1)
            return
        try:
            plan = await db.migrate()
        except MigrationError as exc:
            raise SystemExit(f"migrations refused: {exc}") from None
        print(f"migrations applied: {len(plan.pending)} new, {plan.applied + len(plan.pending)} in total")
    finally:
        await db.close()


async def cmd_sim(args, settings: Settings) -> None:
    from printfarm.simulator.printers import run_simulator

    await run_simulator(settings.fleet())


def main(argv: list[str] | None = None) -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s")
    logging.getLogger("nats").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(prog="printfarm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mg = sub.add_parser("migrate")
    mg.add_argument("--status", action="store_true", help="list applied/pending migrations, change nothing")
    mg.add_argument("--check", action="store_true", help="like --status; exit 1 if anything is pending or wrong")
    a = sub.add_parser("agent")
    a.add_argument("names", nargs="+", choices=AGENTS)
    sub.add_parser("dev")
    sub.add_parser("sim")
    o = sub.add_parser("order")
    o.add_argument("--text", required=True)
    o.add_argument("--qty", type=int, default=1)
    o.add_argument("--material", default="PLA")
    o.add_argument("--color", default="black")
    o.add_argument("--customer", default="walk-in")
    o.add_argument("--due-days", type=int, default=0)
    o.add_argument("--id")
    sub.add_parser("approvals")
    ap_ = sub.add_parser("approve")
    ap_.add_argument("approval_id")
    ap_.add_argument("--reject", action="store_true")
    ap_.add_argument("--note", default="")
    ap_.add_argument("--by", default="owner")
    cb = sub.add_parser("clear-bed")
    cb.add_argument("printer_id")
    cb.add_argument("--by", default="operator")
    sub.add_parser("status")
    w = sub.add_parser("watch")
    w.add_argument("--telemetry", action="store_true")
    args = ap.parse_args(argv)

    handlers = {
        "migrate": cmd_migrate,
        "agent": lambda a, s: run_agents(a.names, s),
        "dev": lambda a, s: run_agents(list(AGENTS), s),
        "sim": cmd_sim,
        "order": cmd_order,
        "approvals": cmd_approvals,
        "approve": cmd_approve,
        "clear-bed": cmd_clear_bed,
        "status": cmd_status,
        "watch": cmd_watch,
    }
    try:
        asyncio.run(handlers[args.cmd](args, settings))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
