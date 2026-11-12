# ADR-0002 · NATS JetStream as the message backbone

**Status:** accepted · 2026-11

**Context.** We need persistent pub/sub, work queues, request/reply, de-duplication, a small KV store and edge buffering, on one host
with little operational effort.

**Options.**

* Kafka: heavy, no work queues or KV built in.
* RabbitMQ: no KV or replay-friendly streams.
* Redis Streams: fewer delivery features; persistence trade-offs.
* MQTT everywhere: no durable work queues or consumer replay.
* **NATS JetStream.**

**Decision.** Use NATS JetStream with four streams: EVENTS (limits), COMMANDS (work queue), TELEMETRY (memory), and DLQ. KV buckets hold
live state. Core request/reply serves queries. MQTT and WebSocket stay at the device edge, behind the Telemetry adapters.

**Consequences.**

* One ~20 MB binary covers all messaging needs.
* Leaf nodes give a clean path to an on-site Telemetry node and a second site.
* The team must learn JetStream consumer semantics; this is documented in 02-communication-protocol.md.
