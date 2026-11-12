# PrintFarm: multi-agent orchestration for a 3D printing business

A Master Coordinator plus specialist agents run a small print farm (Bambu Lab + Klipper/Moonraker, 3-5 printers, 20-50 orders/day) on a
single host. Agents talk over **NATS JetStream** using **CloudEvents** envelopes with typed **Pydantic** payloads. Each agent owns its
data in **PostgreSQL**, and the transactional outbox and inbox make every effect happen exactly once.

| Agent | Kind | Implemented |
|---|---|---|
| Master Coordinator: order sagas, human approvals | deterministic | ✅ |
| Printer Telemetry: Bambu MQTT + Moonraker WebSocket, failure detection, auto-pause | deterministic, real-time | ✅ |
| Fleet Manager: queue, MaxRects bin packing, printer assignment | deterministic | ✅ |
| Inventory: filament ledger, reorder policy, approval-gated POs | deterministic | ✅ |
| Personalization: headless Hebrew/RTL nameplates (OpenSCAD + slicer CLI) | deterministic | ✅ |
| Social Listening: buying intent from human-captured Facebook posts | LLM | 📐 designed (phase 3) |
| Trend Analyst: market shifts | LLM + stats | 📐 designed (phase 3) |

**Design docs:** [docs/architecture/](docs/architecture/README.md) · plan: [docs/PLAN.md](docs/PLAN.md)

## Quick start (Docker)

```bash
docker compose --profile sim up -d --build          # infra + agents + simulated printers
docker compose run --rm cli order --text "משפחת כהן" --qty 3
docker compose run --rm cli watch                   # watch the order flow through the agents
docker compose run --rm cli status
docker compose run --rm cli approvals               # the low-stock reorder waits for you here
docker compose run --rm cli approve <approval-id>
```

Inject a failure to see the safety loop pause a printer:
`SIM_FAULTS="voron-01=thermal_runaway" docker compose --profile sim up -d simulator`

## Local development (no Docker)

You need NATS with JetStream (`nats-server -js`), PostgreSQL 14+, and an MQTT broker for the Bambu simulator (`mosquitto`, or
`amqtt` from the dev extras).

```bash
pip install -e '.[dev]'
export NATS_URL=nats://localhost:4222 DATABASE_URL=postgresql://printfarm:printfarm@localhost:5432/printfarm
# point config/fleet.toml printers at localhost (host = "127.0.0.1", url = "ws://127.0.0.1:7125/websocket")
printfarm sim &          # simulated printers
printfarm dev            # all agents in one process
printfarm order --text "Noa"
```

## Tests

```bash
pytest                                   # unit tests (contracts, saga, packing, detectors, inventory, nameplates, bus policy)
TEST_NATS_URL=nats://localhost:4222 \
TEST_DATABASE_URL=postgresql://printfarm:printfarm@localhost:5432/printfarm \
pytest tests/test_e2e.py                 # full stack: real NATS + Postgres, MQTT + WebSocket simulators
```

The end-to-end test covers this whole flow:

1. A 3-unit Hebrew nameplate order is modelled and all three units are packed onto **one** plate.
2. The plate prints on the MQTT (Bambu) simulator.
3. Filament is deducted, and the purchase order triggered by low stock goes through human approval.
4. A simulated thermal runaway on the WebSocket (Moonraker) printer is detected and the printer is **paused automatically**.

## Layout

```
src/printfarm/
  contracts/      envelope (CloudEvents), message models, registry
  bus/            NATS JetStream bus, in-memory bus, retry/DLQ policy
  store/          Postgres pool, migrations, outbox relay, inbox
  agents/         coordinator, telemetry, fleet, inventory, personalization
  simulator/      Bambu (MQTT) + Moonraker (WebSocket) printer simulators
  cli.py          printfarm command
config/           fleet.toml (printers, materials, policy), mosquitto.conf
docs/             plan, architecture, ADRs
tests/
```

## Not done yet

* Uploading plate files to real printers (Bambu FTPS, Moonraker HTTP upload) and compositing several parts into one plate file. The
  interfaces are in place and the simulator skips this step.
* Social Listening and Trend Analyst agents: contracts and schema exist; implementation is phase 3.
* A dashboard. The CLI covers operations for now.
