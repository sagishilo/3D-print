# ADR-0003 · Printer integration through local protocols

**Status:** accepted · 2026-11

**Decision.**

* Bambu Lab printers are controlled through LAN MQTT over TLS (port 8883, user `bblp`, access code), not the Bambu Cloud API.
* Klipper printers are controlled through Moonraker's JSON-RPC WebSocket.
* One canonical `PrinterState` schema hides vendor differences.
* Only the Telemetry agent talks to printers.

**Why.**

* The safety loop must work with the internet down and react within about one second.
* Cloud APIs add latency, rate limits and account dependency.
* A single gateway gives one place for credentials, idempotency and failure detection.

**Consequences.**

* Telemetry must run on, or have a stable link to, the shop LAN. With a VPS core this means an edge node plus a NATS leaf node.
* File upload uses vendor-specific transports: FTPS (port 990) for Bambu and HTTP `/server/files/upload` for Moonraker.
* Bambu firmware updates can change LAN-mode behaviour, so pin firmware and test it with the simulator contract before updating.
