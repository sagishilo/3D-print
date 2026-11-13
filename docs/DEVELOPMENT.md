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
| Finance (ledger): income, expenses, VAT, per-job cost allocation | deterministic | ✅ |
| Social Listening: buying intent from human-captured Facebook posts | LLM | 📐 designed (phase 10) |
| Trend Analyst: market shifts | LLM + stats | 📐 designed (phase 10) |

**Design docs:** [architecture/](architecture/README.md) · original plan: [PLAN.md](PLAN.md) · dashboard design: [design/](design/)

## Repository layout

```
src/printfarm/        the agents (Python package "printfarm")
  contracts/          envelope (CloudEvents), message models, registry: the single source of every subject name
  bus/                NATS JetStream bus, in-memory bus, retry/DLQ policy
  store/              Postgres pool, migrations, outbox relay, inbox
  agents/             coordinator, telemetry, fleet, inventory, personalization, finance (ledger)
    telemetry/        adapters (Bambu MQTT, Moonraker WS), transfer (FTPS / HTTP upload), platefiles (what may be
                      sent to a printer), safety (verified pause), detectors
  simulator/          printers speaking the real protocols: Bambu (MQTT + implicit FTPS + port-6000 camera),
                      Moonraker (HTTP + WebSocket + Crowsnest webcam on one port), injectable faults
  cli.py              printfarm command
gateway/              FastAPI gateway for the dashboard: auth, commands (app/commands.py), camera proxy, WebSocket bridge;
                      imports printfarm.contracts + printfarm.bus, so it shares the agents' subjects and JetStream topology
bambu-cam/            P1/A1 camera frame-reader for go2rtc
web/                  dashboard (dist/ is served by Caddy)
infra/                nats.conf, Caddyfile, go2rtc.yaml; sim/mediamtx-x1.yml (simulated X1 RTSPS camera)
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
pytest       # unit tests: contracts, saga, packing, detectors, inventory, nameplates, bus policy, ledger, gateway,
             # plate files, pause verification, uploads + cameras against the simulators, bambu-cam
```

The printer-path tests run the real client code against the in-process simulators on `127.0.0.1` (no hardware,
no Docker): `test_sim_transfer.py` (FTPS incl. the TLS-session-reuse rule, Moonraker upload, webcam faults),
`test_adapters_sim.py` (the Moonraker adapter: upload, start, verified pause), `test_bambu_cam.py` (frame-reader vs
the port-6000 camera: refused login, drop, stall) and `test_pause_verify.py` (timing, with a fake clock).

The end-to-end test runs every agent against a real NATS, Postgres and MQTT broker. It **drops** the agent schemas and
streams it uses, so give it throwaway servers. The simplest way (no Windows firewall prompts, Python 3.12 as in production):

```powershell
docker network create pf-e2e
docker run -d --rm --name pf-e2e-nats --network pf-e2e nats:2.10.29-alpine -js
docker run -d --rm --name pf-e2e-pg --network pf-e2e -e POSTGRES_USER=printfarm -e POSTGRES_PASSWORD=e2e -e POSTGRES_DB=printfarm postgres:16.16-alpine
docker run -d --rm --name pf-e2e-mqtt --network pf-e2e eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
docker run --rm --network pf-e2e -v "${PWD}:/src:ro" `
  -e TEST_NATS_URL=nats://pf-e2e-nats:4222 -e TEST_DATABASE_URL=postgresql://printfarm:e2e@pf-e2e-pg:5432/printfarm `
  -e TEST_MQTT_URL=mqtt://pf-e2e-mqtt:1883 python:3.12-slim sh -c `
  "mkdir /work && tar -C /src --exclude=./.venv --exclude=./.git -cf - . | tar -C /work -xf - && pip install -q /work pytest pytest-asyncio && cd /work && python -m pytest tests/test_e2e.py -v"
docker stop pf-e2e-nats pf-e2e-pg pf-e2e-mqtt; docker network rm pf-e2e
```

The end-to-end test covers this whole flow:

1. A 3-unit Hebrew nameplate order is modelled and all three units are packed onto **one** plate.
2. The plate file is uploaded to the MQTT (Bambu) simulator over implicit FTPS. The first upload fails on purpose and is
   retried; the printer checks the md5 before it starts, and the plate prints.
3. Filament is deducted, and the purchase order triggered by low stock goes through human approval.
4. The ledger allocates the plate's filament to its three jobs, books a recorded payment as income with VAT split, and books the
   confirmed purchase order as a cash expense.
5. A simulated thermal runaway on the Moonraker printer (plate uploaded over HTTP) is detected; the printer is **paused
   automatically and the pause is confirmed** from its own state.
6. The same runaway on a second Bambu printer that **ignores the pause** raises a critical **pause unconfirmed** alert.

## Roadmap

See [ROADMAP.md](ROADMAP.md): contract alignment, the printer path on simulators, the dashboard screen by screen, real printers, the
LLM agents and hardening.
