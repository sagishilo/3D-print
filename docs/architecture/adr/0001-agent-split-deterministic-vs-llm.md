# ADR-0001 · Deterministic agents vs. LLM agents

**Status:** accepted · 2026-11

**Context.** Agent systems often put an LLM in every agent. Our agents fall into two groups:

* Some control physical machines and money: printers, purchase orders.
* Others interpret unstructured human text: Facebook posts, trend descriptions, free-form requests.

**Decision.**

* Telemetry, Fleet, Inventory and Personalization are deterministic Python services.
* Social Listening and Trend Analyst use an LLM, and so does the Coordinator's optional front door for free-form requests.
* LLM agents act only by emitting typed commands or events through the same contracts, and their outward-facing actions require human
  approval.
* No LLM call sits on the printer-safety path.

**Consequences.**

* Predictable, unit-testable behaviour where mistakes are costly; LLM cost limited to where it adds value.
* LLM agents are replaceable without touching operations.
* Free-form planning needs an explicit tool surface: the Coordinator's commands.
