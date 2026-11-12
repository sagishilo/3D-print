# 01 · Agent catalog

Each agent is one container (`printfarm agent <name>`), owns one Postgres schema, and communicates only through the bus.
"Subscribes" lists durable consumers; "Emits" lists the messages it writes to its outbox.

---

## Master Coordinator · `coordinator` · deterministic core (+ optional LLM front door)

**Responsibility.** Owns long-running business workflows (*sagas*) and the human-approval register. Decides *what happens next* for
each order. It does not move messages between other agents.

| | |
|---|---|
| Subscribes | `evt.order.order.received`, `evt.personalization.model.>`, `evt.fleet.job.>`, `evt.approval.request.created`, `cmd.coordinator.decide_approval` |
| Emits | `cmd.personalization.generate_nameplate`, `cmd.fleet.enqueue_job`, `evt.order.order.completed`, `evt.order.order.needs_attention`, `evt.approval.request.decided` |
| Owns | `coordinator.sagas`, `coordinator.saga_history`, `coordinator.approvals` |
| Code | `agents/coordinator/saga.py` (pure state machine), `agents/coordinator/agent.py` |

**Order saga.** States: `RECEIVED → MODELING → QUEUED → PRINTING → COMPLETED`, plus `NEEDS_ATTENTION` and `CANCELLED`.
A failed print is re-enqueued under a new job id (`<order>-u<unit>-a<attempt>`). After `MAX_PRINT_ATTEMPTS = 2` the order escalates to a
human. Late events from superseded attempts are ignored by id.

**LLM front door (phase 3).** Free-form requests ("can we do 40 keychains in blue by Friday?") go to an LLM planner that may only call
the Coordinator's typed commands. The LLM proposes; the deterministic saga executes.

**Failure modes.** Coordinator down → orders pause at their current step; nothing is lost because all inputs wait in durable consumers.
Saga rows use `SELECT … FOR UPDATE` plus a `version` column, so concurrent replicas cannot double-advance a saga.

---

## Printer Telemetry Agent · `telemetry` · deterministic, latency-critical

**Responsibility.** The **only** component that talks to printers (printer gateway). It normalises vendor protocols into
`PrinterState`, runs failure detectors, **pauses printers on its own** when a critical failure is detected, and derives lifecycle
events.

| | |
|---|---|
| Device protocols | Bambu Lab LAN MQTT/TLS (`device/<serial>/report`, `…/request`, user `bblp` + access code). Klipper/Moonraker JSON-RPC over WebSocket (`printer.objects.subscribe`, `notify_status_update`) |
| Subscribes | `cmd.telemetry.start_print`, `cmd.telemetry.pause_print` |
| Emits | `evt.printer.print.started`, `evt.printer.print.finished`, `evt.printer.failure.detected`, `evt.printer.connection.changed` (all via outbox); `tlm.<printer>.state` at 1 Hz + KV `printer_status` (direct, best effort) |
| Owns | `telemetry.print_sessions`, `telemetry.failures` |
| Code | `agents/telemetry/adapters.py`, `detectors.py`, `agent.py` |

**Three paths per sample:**

1. **Hot path** (in memory, ≤ 1 sample): adapter → `PrinterState` → detectors → CRITICAL? → `adapter.pause()`.
2. **Warm path** (on change only): lifecycle transitions and failures → Postgres + outbox. Events derived from observed state get
   **deterministic ids** (`uuid5(printer, plate, "finished")`) and a `print_sessions` primary key, so reconnects and restarts never
   duplicate them.
3. **Live view:** throttled to 1 Hz into the TELEMETRY stream (in-memory, kept 1 h) and KV `printer_status`.

**Detectors** (`detectors.py`): thermal runaway (nozzle and bed; armed only once the heater has settled at its target, re-armed when the
target changes), heating failure, progress stall, printer-reported error. Camera-based spaghetti detection plugs in as one more
`Detector` fed by frame scores (Obico-style model). Printers already expose a camera stream.

**Why LAN mode, not Bambu Cloud.** Local MQTT keeps the safety loop working when the internet is down and avoids cloud rate limits. The
Cloud API may still be used read-only for remote viewing.

**Adapter design note.** Adapters hand states to the agent through a queue and never await the agent inline. The agent may answer a
state with a command (pause) whose reply must be read by the same protocol reader. Awaiting inline deadlocks; the end-to-end test caught
exactly this bug.

**Failure modes.** Printer unreachable → reconnect with backoff (1 → 30 s) and `connection.changed(online=false)`; Fleet stops
scheduling onto that printer. `start_print` to an offline printer → immediate `print.finished(succeeded=false, progress=0)` so Fleet
releases the reservation. `start_print` redelivered → no-op if the printer already runs that plate.

---

## Fleet Manager Agent · `fleet` · deterministic, optimisation

**Responsibility.** Owns the print queue. Packs jobs onto build plates and assigns plates to free, compatible printers.

| | |
|---|---|
| Subscribes | `cmd.fleet.enqueue_job`, `cmd.fleet.mark_bed_cleared`, `evt.printer.print.started`, `evt.printer.print.finished`, `evt.printer.connection.changed` |
| Emits | `evt.fleet.job.queued`, `evt.fleet.plate.scheduled`, `cmd.telemetry.start_print`, `evt.fleet.job.started`, `evt.fleet.job.completed`, `evt.fleet.job.failed`, `evt.fleet.plate.finished` |
| Owns | `fleet.printers` (Fleet's view of each printer: online, busy, bed clear, loaded material), `fleet.jobs`, `fleet.plates`, `fleet.plate_items` |
| Code | `agents/fleet/packing.py`, `agents/fleet/agent.py` |

**Two-level optimisation**

1. **Plate packing:** MaxRects with Best-Short-Side-Fit, 90° rotation, an edge margin and spacing between parts. Twelve 120×35 mm
   nameplates fit on a 256 mm Bambu bed.
2. **Assignment:** for each printer that is online, idle and has a clear bed, take queued jobs that match its loaded material and colour,
   in order of `priority DESC, due_by, size DESC`. Add each job greedily while it still fits on the bed and within `max_plate_minutes`.

**Batching window.** Jobs arrive as separate commands. After new work arrives the scheduler waits `batch_window_s` (default 3 s) so that
units of one order, and bursts of orders, can share a plate. The end-to-end test confirms that a 3-unit order lands on one plate.

**Bed clearing.** Neither Bambu nor Klipper printers eject parts. After a successful plate the printer is `bed_clear = false` until an
operator runs `printfarm clear-bed <printer>` (or presses a button on the dashboard). Dev mode sets `auto_clear_bed = true`.

**Concurrency.** `SELECT … FOR UPDATE SKIP LOCKED` on printers and jobs makes `schedule()` safe even with two Fleet replicas.

**Upgrade path (ADR-0005).** Move to an OR-Tools CP-SAT model when there are more than about 8 printers, multi-colour AMS planning, or
due-date penalties across the whole fleet.

---

## Inventory Agent · `inventory` · deterministic

**Responsibility.** Filament ledger per spool, reorder policy, purchase orders, availability queries.

| | |
|---|---|
| Subscribes | `evt.fleet.plate.finished`, `evt.approval.request.decided` |
| Serves | `qry.inventory.availability` (request/reply) |
| Emits | `evt.inventory.filament.consumed`, `evt.inventory.stock.low`, `evt.inventory.reorder.placed`, `evt.approval.request.created` |
| Owns | `inventory.materials`, `inventory.spools`, `inventory.ledger` (append-only), `inventory.purchase_orders` |
| Code | `agents/inventory/model.py` (pure policy), `agents/inventory/agent.py` |

**Consumption.** `plate.finished.grams_used` is the slicer estimate, scaled by progress for failed plates. It is deducted from the spool
loaded on that printer and recorded in the ledger. **Phase 2:** reconcile against actual extrusion where firmware reports it (Klipper
`print_stats.filament_used`) and against periodic spool weighing.

**Reorder policy** (continuous review):
`daily = max(14-day average, baseline)`, `ROP = daily × (lead time + safety days)`,
`order-up-to = daily × (lead time + safety days + review period)`. Purchase orders that are pending or already placed count towards the
stock position, so the same shortage is never ordered twice. If `cost > auto_approve_limit`, a human must approve.

**Supplier integration.** `_placed()` is the hook for an MCP tool server: e-mail the supplier, or call a supplier API or web shop. The
skeleton records the purchase order and emits `reorder.placed`.

---

## Personalization Agent · `personalization` · deterministic, CPU-bound

**Responsibility.** Turns an order's text into a printable artifact with no GUI involved.

| | |
|---|---|
| Subscribes | `cmd.personalization.generate_nameplate` |
| Emits | `evt.personalization.model.ready`, `evt.personalization.model.failed` |
| Owns | `personalization.artifacts`, artifact volume |
| Code | `agents/personalization/nameplate.py`, `agent.py` |

**Pipeline:**

1. Validate the text: NFC normalisation, at most 40 characters, letters and digits in any script plus a short punctuation allow-list.
2. Escape it into an OpenSCAD string literal. This blocks code injection: the customer's text ends up inside source code.
3. Render the parametric template. RTL scripts such as Hebrew are detected and rendered with `direction="rtl"`; the font must have the
   glyphs (the default is Noto Sans Hebrew).
4. `openscad -o .stl`, then `orca-slicer`/`prusa-slicer --export-gcode` if installed.
5. Otherwise, geometric estimates for grams and minutes keep the pipeline flowing in development.

The step is idempotent per `order_id`: a redelivered command re-announces the stored result instead of regenerating it.

**Plate compositing (next step).** When Fleet puts several parts on one plate, a compositor step loads all STLs at the positions in
`fleet.plate_items` into one slicer project, slices it once, and uploads the plate file (FTPS for Bambu, HTTP for Moonraker).

---

## Social Listening Agent · `social` · LLM (phase 3)

**Responsibility.** Find buying intent in posts captured from Facebook groups and turn it into qualified leads with draft replies.

**Compliant ingestion:**

* A **browser extension** used by a real group member. The member clicks "send to PrintFarm" on a post, and the extension POSTs it to a
  small authenticated intake endpoint, which emits `cmd.social.ingest_capture`. There is no background crawling and no credential
  sharing.
* The business's **own Facebook Page and Messenger** through the official Graph API (comments and messages).
* Public sources the business may legally monitor (its own website forms, marketplaces with APIs).

**Processing:**

1. LLM classification returns structured output: `intent_score`, `product_guess`, `summary`.
2. De-duplication by pgvector similarity, so the same request in three groups becomes one lead.
3. Above a threshold: `evt.social.lead.detected` and an LLM-drafted reply.
4. `evt.approval.request.created(action="social.reply")`.
5. **A human posts the reply.** The agent never posts on its own.

| | |
|---|---|
| Subscribes | `cmd.social.ingest_capture`, `evt.approval.request.decided` (action `social.reply`) |
| Emits | `evt.social.lead.detected`, `evt.social.reply.drafted`, `evt.approval.request.created` |
| Owns | `social.captures`, `social.leads` (+ embeddings) |

LLM guardrails: a token budget per day, prompt caching for the classification prompt, batching of captures, and PII minimisation (store
the post text and URL only, never profile data).

---

## Trend Analyst Agent · `trend` · LLM + statistics (phase 3)

**Responsibility.** Detect demand shifts and feed them back into operations.

**Inputs:**

* Our own sales: orders by product, text pattern, colour and season.
* Lead topics from Social Listening.
* Public trend sources accessed through MCP tool servers, such as Google Trends exports and marketplace APIs (Etsy Open API).

**Method:** statistics first, LLM second. Seasonal decomposition and change-point detection on counts produce candidate signals. The LLM
summarises and names them ("Hebrew family-name door signs rising before Rosh Hashana").

**Outputs:**

* `evt.trend.signal.detected`, consumed by Inventory to raise `baseline_daily_grams` for rising colours.
* Product-template suggestions for Personalization.
* A weekly report stored as an artifact.

Runs on a schedule (a KV lease ensures one active instance) and never on the critical path.
