# Roadmap: one stack, then dashboard, agents and real printers

Status: **Phases 0–1 done; Phase 2 next.** Agreed decisions: the backend's contracts are canonical; no real printers yet (work against
the simulators); after alignment comes an early printer-path test, then the dashboard screen by screen; orders move through operator
gates with per-stage auto-advance switches (off by default).

## Why: what the review of the merged code found

| # | Finding | Where |
|---|---|---|
| F1 | Both halves create the JetStream streams `EVENTS`, `TELEMETRY`, `DLQ` with different configs (TELEMETRY memory vs file), so whichever starts second fails and the EVENTS subject list flips. | `src/printfarm/bus/nats_bus.py`, `common/pf_common/` |
| F2 | Three naming schemes (backend `evt.*`/`cmd.*`, stack `print.job.*`, design mock `fleet.slice.dispatch`); the stack's "confirmed" subjects don't exist in the backend. | `contracts/messages.py`, `subjects.py` |
| F3 | Finance expects flat JSON; the backend publishes CloudEvents envelopes (payload under `data`). | `finance/app/main.py` |
| F4 | Finance's inputs don't exist: no payment events; POs carry a float estimate, no supplier/VAT/actual total; grams and print time exist only per plate. | `messages.py`, inventory agent |
| F5 | Money as `REAL` in the backend vs integer agorot in Finance. | `001_init.sql` |
| F6 | The WebSocket allow-list forwards command subjects (`print.job.` covers `print.job.create`, …). | `gateway/app/main.py` |
| F7 | The gateway sends commands with core request/reply; backend commands are JetStream work-queue messages. | gateway, `nats_bus.py` |
| F8 | Finance's durable consumer ignores changed filter subjects once it exists. | `finance/app/main.py` |
| F9 | Auto-pause is never verified; newer Bambu firmware may reject third-party control (LAN Developer Mode). | telemetry agent |
| F10 | No plate upload to printers, no multi-part plate compositor. | telemetry adapters |
| F11 | No PO receive / spool load; design's reorder rule differs from the policy model. | inventory |
| F12 | Design needs catalog SKUs, priced multi-item orders, QC/Shipped, receivables, audited toggles, DLQ replay, … | design vs saga |
| F13 | Design's "groups monitored · last scan" contradicts ADR-0007 (human capture only). | ADR-0007 |

## Phases

| Phase | Scope | Done when |
|---|---|---|
| 0 | One repository: stack files, merged compose (agents + `sim` profile + `cli`), `fleet.sim.toml` / `fleet.toml`, docs. | tests green |
| 1 ✅ | One contract and topology: backend contracts canonical; `pf_common` removed; Finance as an agent on CloudEvents; gateway commands → JetStream (validated, audited, 202, idempotent); WebSocket allows only `evt.`/`tlm.`/`dlq.`; new commands/events (payments, PO ordered/received, spool load, resume/cancel/pause-all, config); `PlateFinished` with print time and per-job shares; money in agorot; one migration path. Also fixed: NATS passwords starting with a digit stopped NATS from starting; non-ASCII login/CSRF input crashed the gateway. | full stack runs with `--profile sim`; an order ends in ledger rows; tests green (done) |
| 2 | Printer path on simulators: FTPS/HTTP upload + start/resume/stop in the adapters, pause verification (`ignore_commands` fault), fake cameras per printer, bambu-cam fixes. | e2e upload→print on both vendors; unconfirmed pause raises an alert |
| 3 | Dashboard foundation: Vite/React/TS app built into the Caddy image, auth flow, app shell, WebSocket with replay, System & Agents screen (heartbeats, lag, DLQ replay, audited toggles). | Playwright: login, agents healthy, toggle, DLQ replay |
| 4 | Printer Fleet screen: live tiles, cameras, pause/resume/cancel, failure log. | |
| 5 | Orders v2 + Order Pipeline: priced multi-item orders, saga with LEAD…QC…SHIPPED, drag = dispatch, auto-advance switches. | |
| 6 | Filament & Reorder: spools, rules (Auto/Manual), POs (approve, confirm ordered, receive). | |
| 7 | Finance screen: P&L, income, expenses, product margins, receivables, VAT, settings, CSV. | |
| 8 | Instant Catalog + automated slicing (OrcaSlicer CLI spike; fallback: operator-sliced templates). | |
| 9 | Real printers (when available): preflight login checks, one printer first, then all; cameras; quiet hours. | real print end to end; verified pause |
| 10 | Social Listening + Trend Analyst (Claude API) + capture extension; human-approved, human-posted replies only. | labelled-set accuracy check |
| 11 | Hardening: NATS per-user permissions, backups, observability, CI. | |
