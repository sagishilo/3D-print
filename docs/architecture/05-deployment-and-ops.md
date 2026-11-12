# 05 · Deployment and operations

## 1. Topology

One host running Docker Compose (`docker-compose.yml`):

| Container | Image | Notes |
|---|---|---|
| `nats` | nats:2.10 | JetStream file store on a volume; monitoring on :8222 |
| `postgres` | postgres:16 | volume + nightly dump |
| `migrate` | app | one-shot; agents wait for it |
| `coordinator`, `personalization`, `fleet`, `inventory`, `telemetry` | app | `printfarm agent <name>`; `restart: unless-stopped` |
| `mosquitto` + `simulator` | dev only | `--profile sim` |

**Where to run it:**

* **Mini-PC on the shop LAN** (recommended for the printer-facing parts). Telemetry needs low-latency LAN access to the printers, and
  safety must keep working when the internet is down.
* **VPS:** fine for every agent except Telemetry. If the core runs on a VPS, run Telemetry on a small device in the shop (e.g. a
  Raspberry Pi 5). Connect it with a NATS **leaf node** that buffers through outages and replicates when the link returns. The hot
  safety path stays local in both cases.

Resources at the target scale: 2 vCPU / 4 GB RAM is plenty. OpenSCAD renders take about 1-5 s each.

## 2. Configuration and secrets

* `config/fleet.toml`: printers, materials, reorder policy, initial spools, scheduler knobs.
* Environment: `NATS_URL`, `DATABASE_URL`, `FLEET_CONFIG`, `ARTIFACT_DIR`, `LOG_LEVEL`.
* Secrets go only in a non-committed `.env` / `env_file` per container: Bambu access codes (`access_code_env` names the variable), NATS
  user credentials per agent, and the LLM API key (only for the `social` and `trend` containers).

## 3. Observability

| Signal | Implementation |
|---|---|
| Logs | stdout, one line per message emitted/consumed, with `corr=<correlationid>` |
| Event trail | `printfarm watch` (live), `coordinator.saga_history`, the EVENTS stream (30 days) |
| Liveness | KV `heartbeats` (TTL 30 s); compose restarts crashed containers |
| Printer view | `printfarm status` (KV `printer_status`); TELEMETRY stream for charts |
| Traces (phase 2) | OpenTelemetry: `traceparent` is already carried in the envelope; export to Grafana Tempo/Jaeger |
| Metrics (phase 2) | NATS `/varz` + `/jsz`, consumer lag, outbox backlog (`count(*) WHERE published_at IS NULL`), DLQ count |

**Alerts worth having on day one:**

* any message in DLQ;
* a heartbeat missing for more than 60 s;
* a printer offline while it has a plate scheduled;
* `failure.detected` with severity critical (push notification to the owner's phone);
* an approval pending for more than 4 h.

## 4. Operations runbook (CLI)

```bash
printfarm order --text "משפחת כהן" --qty 2 --color white   # manual order
printfarm status                                           # printers + open orders
printfarm approvals                                        # pending human decisions
printfarm approve apr-1234abcd [--reject --note "..."]
printfarm clear-bed bambu-01                               # after removing a finished plate
printfarm watch [--telemetry]                              # live event stream
```

## 5. Security

* Printers: LAN mode only. Use TLS to Bambu, pinning its certificate in production. Never expose printer ports to the internet. Put the
  shop LAN on its own VLAN.
* Bus: one NATS user per agent, with subject permissions restricted to what [01-agent-catalog.md](01-agent-catalog.md) lists.
* Database: one role per agent (schema-scoped grants); the CLI's read-only commands use a read-only role.
* Input handling: customer text is validated and escaped before it enters OpenSCAD source. External content (social posts, trend data)
  is treated as untrusted data in LLM prompts: it is never used as instructions, and LLM agents can only call whitelisted tools.
* Audit: `saga_history`, `approvals` (who decided what and when), `telemetry.failures`, the ledger, and the EVENTS stream.

## 6. Scaling path

| Trigger | Change |
|---|---|
| More than ~8 printers, or multi-colour AMS planning | Fleet assignment → OR-Tools CP-SAT (ADR-0005) |
| Hundreds of orders/day, complex compensations | Saga engine → Temporal (ADR-0004) |
| Artifacts must be reachable from another host | Artifact volume → MinIO/S3 (ADR-0006) |
| Second site / shop | NATS leaf node per site; Telemetry + Fleet per site; Coordinator and Inventory central |
| Agent CPU-bound (e.g. rendering bursts) | `docker compose up --scale personalization=3`; durable consumers load-balance |
