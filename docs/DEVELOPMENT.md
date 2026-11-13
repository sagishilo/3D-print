# PrintFarm: development guide

A Master Coordinator plus specialist agents run a small print farm (Bambu Lab + Klipper/Moonraker, 3-5 printers, 20-50 orders/day) on a
single host. Agents talk over **NATS JetStream** using **CloudEvents** envelopes with typed **Pydantic** payloads. Each agent owns its
data in **PostgreSQL**, and the transactional outbox and inbox make every effect happen exactly once.

Operating the stack (installing, secrets, HTTPS, cameras, printers) is covered in the [README](../README.md). This page is for working on
the code.

| Agent | Kind | Implemented |
|---|---|---|
| Master Coordinator: order sagas, human approvals | deterministic | ✅ |
| Printer Telemetry: Bambu MQTT + Moonraker WebSocket, failure detection, auto-pause | deterministic, real-time | ✅ |
| Fleet Manager: queue, MaxRects bin packing, printer assignment | deterministic | ✅ |
| Inventory: filament ledger, reorder policy, approval-gated POs | deterministic | ✅ |
| Personalization: headless Hebrew/RTL nameplates (OpenSCAD + slicer CLI) | deterministic | ✅ |
| Social Listening: buying intent from human-captured Facebook posts | LLM | 📐 designed (phase 10) |
| Trend Analyst: market shifts | LLM + stats | 📐 designed (phase 10) |

**Design docs:** [architecture/](architecture/README.md) · original plan: [PLAN.md](PLAN.md) · dashboard design: [design/](design/)

## Repository layout

```
src/printfarm/        the agents (Python package "printfarm")
  contracts/          envelope (CloudEvents), message models, registry: the single source of every subject name
  bus/                NATS JetStream bus, in-memory bus, retry/DLQ policy
  store/              Postgres pool, migrations, outbox relay, inbox
  agents/             coordinator, telemetry, fleet, inventory, personalization
  simulator/          Bambu (MQTT) + Moonraker (WebSocket) printer simulators
  cli.py              printfarm command
gateway/              FastAPI gateway for the dashboard: auth, commands, camera proxy, WebSocket bridge
finance/              Ledger service (income, expenses, VAT)
common/pf_common/     shared NATS subject names for gateway + finance (being replaced by printfarm.contracts)
bambu-cam/            P1/A1 camera frame-reader for go2rtc
web/                  dashboard (dist/ is served by Caddy)
infra/                nats.conf, Caddyfile, go2rtc.yaml
db/init/              first-start SQL for gateway + finance tables
config/               fleet.sim.toml (simulated printers), fleet.toml (your printers), mosquitto.conf
scripts/              init_env.py, check_env.py, preflight.py
docs/                 plan, architecture, ADRs, design
tests/
```

## Run everything in Docker (simulated printers)

Create `.env` first (README steps 3–4), then:

```bash
docker compose --profile sim up -d --build          # infra + gateway + agents + simulated printers
docker compose run --rm cli order --text "משפחת כהן" --qty 3
docker compose run --rm cli watch                   # watch the order flow through the agents
docker compose run --rm cli status
docker compose run --rm cli approvals               # the low-stock reorder waits for you here
docker compose run --rm cli approve <approval-id>
```

Inject a failure to see the safety loop pause a printer:
`SIM_FAULTS="voron-01=thermal_runaway" docker compose --profile sim up -d simulator`

## Local development (agents outside Docker)

Use Python 3.12 (the version in the Docker images). You need NATS with JetStream, PostgreSQL 14+, and an MQTT broker for the Bambu
simulator. The easiest way is to run only the infrastructure from compose (`docker compose up -d nats postgres`) and the agents locally.
The compose NATS requires a user; agents use the `agents` user from `.env`.

```bash
pip install -e '.[dev]'
export NATS_URL=nats://agents:<NATS_AGENTS_PASSWORD>@127.0.0.1:4222
export DATABASE_URL=postgresql://printfarm:<POSTGRES_PASSWORD>@127.0.0.1:5432/printfarm
# copy config/fleet.sim.toml to config/fleet.local.toml and point its printers at 127.0.0.1
# (host = "127.0.0.1" for Bambu, url = "ws://127.0.0.1:7125/websocket" for Moonraker)
export FLEET_CONFIG=config/fleet.local.toml
printfarm sim &          # simulated printers
printfarm dev            # all agents in one process
printfarm order --text "Noa"
```

`amqtt` (dev extra, a pure-Python MQTT broker for tests) does not build on Python 3.15; use 3.12, or run mosquitto.

## Tests

```bash
pytest                                   # unit tests (contracts, saga, packing, detectors, inventory, nameplates, bus policy)
TEST_NATS_URL=nats://services:<pw>@127.0.0.1:4222 \
TEST_DATABASE_URL=postgresql://printfarm:<pw>@127.0.0.1:5432/printfarm \
pytest tests/test_e2e.py                 # full stack: real NATS + Postgres, MQTT + WebSocket simulators
```

The end-to-end test covers this whole flow:

1. A 3-unit Hebrew nameplate order is modelled and all three units are packed onto **one** plate.
2. The plate prints on the MQTT (Bambu) simulator.
3. Filament is deducted, and the purchase order triggered by low stock goes through human approval.
4. A simulated thermal runaway on the WebSocket (Moonraker) printer is detected and the printer is **paused automatically**.

## Roadmap

See [ROADMAP.md](ROADMAP.md): contract alignment, the printer path on simulators, the dashboard screen by screen, real printers, the
LLM agents and hardening.
