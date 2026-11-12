# ADR-0004 · Postgres-backed saga state machine instead of Temporal (for now)

**Status:** accepted · 2026-11

**Context.** 20-50 orders/day, one main workflow (order → model → print → done), a single host.

**Decision.**

* Sagas are rows in `coordinator.sagas`, advanced by a pure `decide(state, context, message)` function.
* Each step commits in one transaction together with the inbox claim and the outbox.
* `saga_history` provides the audit trail.

**Consequences.**

* No extra infrastructure, and the state machine is trivially unit-testable.
* Timers (SLA timeouts) need a small scanner loop.
* Complex compensation logic would get harder to manage.

**Revisit when:** sustained hundreds of orders/day, more than about 5 saga types, long-running timers, or a need to version workflows.
The `decide` functions port directly to Temporal workflow code.
