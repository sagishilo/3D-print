# 02 · Inter-agent communication protocol

## 1. Layers

| Layer | Technology | Used for |
|---|---|---|
| Device | MQTT (Bambu LAN, TLS :8883), JSON-RPC 2.0 over WebSocket (Moonraker :7125) | Printer ↔ Telemetry agent only |
| Backbone | **NATS JetStream** | Every agent ↔ agent message |
| Tools | **MCP tool servers** | LLM agents ↔ outside systems (Facebook Page API, supplier e-mail/API, Etsy, Google Trends export) |
| Human | CLI today; a dashboard later | Orders, approvals, bed-cleared confirmations |

Why NATS: one ~20 MB binary gives persistent streams, work queues, request/reply, a KV store, de-duplication and per-subject limits, so a
single host needs no separate Kafka, Redis or RabbitMQ (ADR-0002).

## 2. Envelope: CloudEvents 1.0, structured JSON mode

Every message is one `Envelope` (`contracts/envelope.py`):

```json
{
  "specversion": "1.0",
  "id": "8c1d…",                                  // unique; also JetStream Nats-Msg-Id (publish de-dup)
  "source": "/agents/fleet",
  "type": "io.printfarm.fleet.job.completed.v1",  // = payload model + version
  "subject": "ord-1a2b",                          // the entity the message is about (optional)
  "time": "2026-11-12T22:35:32Z",
  "datacontenttype": "application/json",
  "kind": "event",                                // event | command | reply
  "correlationid": "ord-1a2b",                    // business transaction (saga) id; constant across a workflow
  "causationid": "51fe…",                         // id of the message that directly caused this one
  "idempotencykey": null,                         // optional business-level de-dup key
  "deadline": null,                               // commands: never execute after this instant
  "traceparent": "00-…",                          // W3C trace context (OpenTelemetry)
  "data": { "job_id": "ord-1a2b-u1-a1", "order_id": "ord-1a2b", "plate_id": "plate-…" }
}
```

* `data` is validated against a **Pydantic v2 model** (`contracts/messages.py`, `extra="forbid"`, frozen). The registry maps
  `type ↔ subject ↔ model`, and duplicate types fail at import time.
* The `correlationid` / `causationid` pair turns any order into a causal tree you can query: `saga_history` stores the message ids, and
  `printfarm watch` prints `corr=`.
* **Correlation scopes.** Order-level messages use `correlationid = order_id`. A plate combines jobs from several orders, so
  plate-level messages correlate on `plate_id`. Fleet stores each job's order correlation id and restores it on job-level events
  (`job.started|completed|failed`).

## 3. Subject taxonomy

| Prefix | Pattern | Stream | Retention | Semantics |
|---|---|---|---|---|
| `evt.` | `evt.<domain>.<entity>.<past-verb>` | EVENTS | file, 30 days, limits | Facts. Fan-out: every interested agent has its own durable consumer |
| `cmd.` | `cmd.<owner-agent>.<imperative>` | COMMANDS | file, 7 days, **work queue** | Requests to exactly one agent; deleted once acknowledged |
| `tlm.` | `tlm.<printer>.state` | TELEMETRY | **memory**, 1 hour, 3600 msgs/printer | Live printer state at 1 Hz; replayable for charts |
| `qry.` | `qry.<owner-agent>.<noun>` | — (core NATS) | none | Synchronous request/reply for reads (e.g. `qry.inventory.availability`) |
| `dlq.` | `dlq.message.dead_lettered` | DLQ | file, 90 days | Poison messages wrapped with error, consumer and delivery count |

Full catalogue: [`contracts/messages.py`](../../src/printfarm/contracts/messages.py). Rules:

* **Commands are owned by their receiver.** Only Fleet consumes `cmd.fleet.*`. A sender never assumes *how* a command is carried out;
  it waits for the resulting event (`cmd.fleet.enqueue_job` → `evt.fleet.job.queued`).
* **Workflow steps never use request/reply.** Steps are command → event, so the workflow survives restarts. Request/reply is only for
  reads where a stale or failed answer is harmless.
* **Events are past-tense facts** that any number of agents may consume. The producer never knows its consumers.

## 4. Delivery guarantees

```mermaid
sequenceDiagram
  autonumber
  participant P as Producer agent
  participant PDB as Postgres (producer)
  participant JS as JetStream
  participant C as Consumer agent
  participant CDB as Postgres (consumer)

  P->>PDB: BEGIN; business write; INSERT outbox; COMMIT
  loop OutboxRelay (≤250 ms or immediate wake-up)
    P->>PDB: SELECT … FOR UPDATE SKIP LOCKED
    P->>JS: publish(subject, envelope, Nats-Msg-Id = id)
    Note over JS: duplicate within 2 min → dropped
    P->>PDB: UPDATE outbox SET published_at
  end
  JS->>C: deliver (pull consumer, explicit ack)
  C->>CDB: BEGIN; INSERT inbox(consumer, id) ON CONFLICT DO NOTHING
  alt already processed
    C->>CDB: COMMIT (no-op)
  else first time
    C->>CDB: handler writes + INSERT outbox; COMMIT
  end
  C->>JS: ack
```

| Failure | What happens |
|---|---|
| Producer crashes after commit, before publish | Relay publishes on restart (outbox row still pending) |
| Crash after publish, before `published_at` | Re-published with the same id → JetStream de-dup window drops it; beyond the window the consumer inbox drops it |
| Consumer crashes mid-handler | Transaction rolls back, no ack → JetStream redelivers after `ack_wait` (60 s) |
| Consumer crashes after commit, before ack | Redelivered → inbox hit → no-op → ack |
| Handler raises | `nak` with backoff 1 s, 5 s, 15 s, 60 s; after `max_deliver` (5) → `DeadLettered` to `dlq.*` + ack |
| Message cannot be parsed | `term` (never redelivered) + error log |
| Command past `deadline` | Acknowledged and dropped without running |
| Same fact observed twice by Telemetry (reconnect/restart) | Deterministic id (`uuid5(printer, plate, "finished")`) + `print_sessions` PK → emitted once |

Result: **at-least-once delivery, exactly-once effects** per consumer.

**Ordering.** Each durable consumer processes its messages sequentially, so ordering holds per consumer. Business logic never relies on
global ordering. The saga tolerates out-of-order and stale events by checking job ids and attempts.

**Side effects on devices.** Device commands cannot join a database transaction. They are made idempotent at the device instead: the
plate id is the print's name, and Telemetry skips `start_print` when the printer already runs that plate.

## 5. Consumers

* Durable name = `<agent>__<subject filter>` (e.g. `fleet__evt_printer_print_finished`), one per handler.
* New event consumers start at `DeliverPolicy.NEW`. Command consumers start at `ALL`, so commands sent while an agent was down are not
  lost.
* Scaling: run a second replica of an agent with the same durable name. JetStream load-balances pull consumers, and the inbox and row
  locks keep effects correct.

## 6. Request/reply (queries)

Core NATS request with a 2 s timeout, served on a queue group named after the subject. The reply is an `Envelope` with `kind: "reply"`
and `causationid` = the request id. Callers must have a fallback for a timeout; queries never block a workflow.

## 7. Versioning

* `type` ends in `.v<N>`. Adding an optional field is non-breaking: same version, and consumers ignore what they do not know. Note that
  `extra="forbid"` means *producers* must be upgraded after consumers.
* A breaking change gets a new model class with `version = 2` published **alongside** v1 until all consumers have migrated. Then v1 is
  removed. The registry rejects two models claiming the same type.

## 8. Human-in-the-loop protocol

```
<agent>        -- evt.approval.request.created {approval_id, action, summary, details} -->  coordinator (stores, notifies)
human (CLI)    -- cmd.coordinator.decide_approval {approval_id, approved, decided_by}  -->  coordinator (validates still pending)
coordinator    -- evt.approval.request.decided {approval_id, action, approved}         -->  <agent> (filters by action, acts)
```

Only the Coordinator turns a decision into the authoritative `ApprovalDecided`. A decision for an approval that is no longer pending is
ignored, so a double click or a replay cannot approve twice. Actions: `inventory.reorder` (implemented), `social.reply`,
`printer.cancel` (phase 3).

## 9. Security of the bus

* NATS accounts and users **per agent**, with publish/subscribe permissions matching the tables in
  [01-agent-catalog.md](01-agent-catalog.md). For example, only `telemetry` may publish `evt.printer.>` and `tlm.>`, and only humans'
  credentials may publish `cmd.coordinator.decide_approval`.
* TLS on NATS whenever any client is outside the host (e.g. the browser-extension intake).
* Printer access codes live only in the Telemetry container's environment (an env file, never committed).
