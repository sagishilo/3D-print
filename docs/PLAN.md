# Plan: Multi-Agent Orchestration Platform for a 3D Printing Business

Status: **Draft for review.** No architecture document or code has been written yet.
Once this plan is approved, it turns into `docs/architecture/` (the design) and `contracts/` (Python message schemas).

---

## 1. Goal and scope

Design a platform where a **Master Coordinator** hands work to six specialist agents:

| Agent | Job | Speed it needs |
|---|---|---|
| Social Listening | Find posts in Facebook groups where someone wants to buy | Minutes |
| Trend Analyst | Spot shifts in demand (products, colors, seasons) | Hours to days |
| Printer Telemetry | Read live printer feeds (WebSocket / MQTT) and catch failures | **Under 1 second** |
| Fleet Manager | Pack parts onto build plates and assign plates to printers | Seconds to minutes |
| Inventory | Track filament used, forecast, and reorder | Minutes to hours |
| Personalization | Generate custom nameplates headlessly (model, slice, ready to print) | Seconds per order |

The design must spell out the **inter-agent communication protocol** and **state management**.

Out of scope for the first version: payments, storefront UI, shipping labels. These show up only as integration points.

---

## 2. Main design decisions (proposed; to be written up as ADRs)

1. **Two kinds of agent, not one.**
   - *Deterministic agents* (Telemetry, Fleet Manager, Inventory, Personalization) are ordinary Python services. They respond to events and must stay predictable.
   - *Reasoning agents* (Coordinator for unstructured requests, Social Listening, Trend Analyst) call an LLM.
   - The LLM is **never** in the hot path for printer safety. A failure detected by telemetry goes from the edge straight to pausing the printer, without waiting on the Coordinator.

2. **The Coordinator orchestrates sagas; it does not relay messages.** It owns long-running business workflows, such as *lead → quote → order → personalize → schedule → print → QC → ship*, as durable state machines. Agents publish domain events directly on the bus, so the Coordinator is not a single bottleneck.

3. **Communication protocol: a hybrid with three layers.**
   - **Edge:** MQTT (Bambu Lab local MQTT/TLS) and WebSocket (Klipper/Moonraker JSON-RPC, OctoPrint). Printer adapters normalize these into one canonical telemetry schema.
   - **Backbone:** NATS JetStream. Subjects carry commands (`cmd.<agent>.<action>`, point-to-point with acknowledgement), events (`evt.<domain>.<event>`, publish/subscribe) and queries (request/reply).
   - **Envelope:** CloudEvents 1.0, with `correlation_id`, `causation_id`, `idempotency_key`, `deadline` and `schema_version`. Payloads are Pydantic v2 models, versioned in `contracts/`.
   - Agents reach outside systems (Facebook, supplier sites, slicer CLI, Etsy) through **MCP tool servers**, so each tool's access and credentials stay limited to the agent that needs it.

4. **State management: each agent owns its own data.**
   - **PostgreSQL** is the system of record. There is one schema per agent (leads, trends, printers, jobs/plates, spools/orders, personalization), and no agent writes to another agent's tables.
   - A **transactional outbox** makes each database write and its event publication atomic. Consumers use **inbox / idempotency** tables so a redelivered message is not processed twice.
   - **NATS KV** holds fast-changing shared state: live printer status, heartbeats, printer leases and locks.
   - **Saga state** lives in Postgres as Coordinator-owned state machine tables. Temporal is the upgrade path; see open question 3.
   - **Object storage** (S3/MinIO) holds STL/3MF/G-code files, camera frames and trend reports.
   - **Vector store** (pgvector) holds embeddings of social posts and trend signals for deduplication and similarity search.

5. **Algorithms, chosen per agent.**
   - Fleet Manager: two levels of optimization. First, 2D plate packing of part footprints (a heuristic such as MaxRects). Second, assignment of plates to printers with OR-Tools CP-SAT, respecting material, color/AMS slots, nozzle size, bed size, due dates and operator shifts for clearing plates.
   - Telemetry: rule-based detectors (thermal runaway, extruder stall, layer shift, filament runout, stalled progress) plus optional camera-based spaghetti detection.
   - Inventory: tracks grams per spool, combining the slicer's estimate with actual extrusion reported by telemetry. Reorder point = demand during lead time + safety stock. Purchases above a set amount need human approval.
   - Personalization: a CadQuery parametric template (Python), then OrcaSlicer/PrusaSlicer CLI to slice, then a 3MF/G-code file with filament and time estimates, passed to Fleet Manager.

6. **Human-in-the-loop gates** for any action that leaves the business: replying to a Facebook post, placing a supplier order above a threshold, cancelling a print without a confirmed safety failure.

---

## 3. Constraints and risks to address in the design

- **Facebook groups access (the biggest risk).** Meta removed the Groups API from the Graph API in 2024, and scraping breaks Meta's terms. The design will offer compliant ingestion options: human-assisted capture through a browser extension used by real group members, licensed third-party social data providers, and the Graph API for the business's own Page and Messenger. Replies to posts are never sent automatically.
- Printer protocols differ by vendor, so a pluggable adapter layer is required.
- LLM cost and latency: budgets per agent, caching, and batch classification.
- Network splits between the shop floor and the cloud: an edge gateway that keeps detecting failures while offline.
- Security: credentials scoped per agent, signed commands to printers, and an audit log of every action taken by an agent.

---

## 4. Deliverables (after approval)

```
docs/
  PLAN.md                         ← this file
  architecture/
    ARCHITECTURE.md               ← main design document
    01-context-and-goals.md
    02-agent-catalog.md           ← one section per agent: duties, inputs/outputs, tools, SLOs, failure modes
    03-communication-protocol.md  ← envelope, subject naming, delivery rules, request/reply, retries, DLQ, versioning
    04-state-management.md        ← data ownership, outbox/inbox, KV, sagas, consistency model
    05-workflows.md               ← sequence diagrams: lead→order, nameplate order, print failure, reorder
    06-deployment-and-ops.md      ← edge vs. cloud, observability (OpenTelemetry), security, scaling
    adr/                          ← one ADR per decision in section 2
contracts/                        ← Python (Pydantic v2) message schemas + subject registry
  envelope.py
  events/*.py
  commands/*.py
```

Diagrams will be written in Mermaid so they render on GitHub.

Optional phase 2, if you want it: a runnable Python skeleton (Coordinator + 6 agent stubs + NATS + a simulated printer) to prove the protocol works end to end.

---

## 5. Open questions for you

1. **Printer fleet:** which brands and models? Bambu Lab, Prusa, Klipper-based, a mix? This determines the adapters and whether MQTT or WebSocket is the main protocol.
2. **Scale:** roughly how many printers and orders per day? This decides between a single host (Docker Compose) and Kubernetes.
3. **Workflow engine:** a lightweight Postgres state machine (proposed for simplicity) or Temporal from day one?
4. **Facebook ingestion:** are you a member or admin of the target groups, and is human-assisted capture acceptable?
5. **Deliverable depth:** design docs + Python contracts only, or also the phase-2 runnable skeleton?
