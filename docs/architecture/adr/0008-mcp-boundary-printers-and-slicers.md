# ADR-0008 · Where MCP fits: not in the printer control path

**Status:** accepted · 2026-11 (architecture check at the start of Phase 5)

**Context.**

* Printers are reached only by the Telemetry agent, over local protocols (ADR-0003):
  * Bambu: MQTT over TLS on 8883 (user `bblp` + LAN access code) for state and control; implicit FTPS on 990, with TLS
    session reuse, for plate files; cameras via go2rtc (X1 RTSPS) or `bambu-cam` (P1/A1 port 6000).
  * Klipper: Moonraker JSON-RPC over WebSocket, HTTP upload.
  * Every command to a printer goes through NATS JetStream (`cmd.telemetry.*`): validated against a contract, audited by
    the gateway, idempotent, expiring after 30 s, and verified against the printer's own state (pauses, calibrations).
* No MCP server exists in the code today. The architecture documents plan MCP only for the LLM agents' outside
  tools (Facebook Page API, supplier e-mail/API, Etsy, Google Trends exports).
* Community MCP servers for Bambu printers exist (for example `schwarztim/bambu-mcp`, `synman/bambu-mcp`, Bambu-only
  forks of `mcp-3D-printer-server`). They are unofficial and talk MQTT/FTPS to the printer directly.
* Printpal's PrintWatch is an AI failure-detection service (cloud API with an API key; a local on-premise option is
  sold; spaghetti detection is its proven case; it powers SimplyPrint's detection). It is not an MCP server.

**Decision.**

1. **No MCP server talks to printers.** A tool server with its own MQTT/FTPS connection would bypass the single
   writer (Telemetry), verified pauses, command expiry, the audit log and the calibration guards, and would hold the
   printers' access codes. Third-party Bambu MCP servers are therefore not used.
2. **When LLM agents need printer or order actions (Phase 10), they get one PrintFarm MCP server that wraps the
   gateway's commands** (`printers.pause`, `orders.advance`, ...) and read endpoints. Each tool call becomes the same
   validated, audited, idempotent command a person sends from the dashboard, under a dedicated, least-privilege
   identity (no calibration, no cancel, no config changes). This standardizes tool calls across agents without a
   second control path.
3. **Slicing stays a deterministic agent step** (Phase 8: OrcaSlicer CLI inside the Personalization agent or a slicer
   container), not an MCP tool: it has no LLM caller, and its inputs and outputs are files in the artifact store.
4. **Vision failure detection (PrintWatch or a self-hosted detector) plugs in as a detector**, not a tool: it reads
   go2rtc snapshots and emits `evt.printer.failure.detected` with a severity, so the existing auto-pause and verified
   pause apply. A cloud detector sends camera frames off site and costs per printer: decide in Phase 9; prefer a
   local model if frames must stay on the LAN.

**Consequences.**

* MCP appears only at the edge between LLM agents and the outside world (and, later, the gateway). The device layer,
  its safety loop and its credentials stay in one place.
* Adding a printer brand means a new Telemetry adapter (canonical `PrinterState`, calibrate/abort), not a new MCP server.
