"""Who may publish what on NATS. The single source for ``infra/nats/nats.conf`` (a test keeps the two equal).

Agents publish events, telemetry, dead letters and the few commands one agent sends another (the saga asks
personalization for a model and fleet for jobs; fleet asks telemetry to start a plate). Every other command is an
operator decision and comes only from the gateway (the ``services`` user), after login, CSRF and the audit log:
an agent, or anyone holding the agents' password, cannot approve a purchase order, record a payment, change a
setting or cancel a print.
"""
from __future__ import annotations

# Commands an agent may send (exact subjects). Everything else under cmd.> is operator-only.
AGENT_COMMANDS: frozenset[str] = frozenset({
    "cmd.personalization.generate_nameplate",   # coordinator saga
    "cmd.personalization.prepare_catalog_item",  # coordinator saga
    "cmd.fleet.enqueue_job",                     # coordinator saga
    "cmd.fleet.cancel_jobs",                     # coordinator saga
    "cmd.telemetry.start_print",                 # fleet scheduler
})

# Non-command subjects agents publish: events, telemetry, dead letters, query replies and the JetStream/KV/object
# store APIs their client library uses.
AGENT_PUBLISH: tuple[str, ...] = (
    "evt.>", "tlm.>", "dlq.>", "qry.>", "_INBOX.>",
    "$JS.API.>", "$JS.ACK.>", "$JS.FC.>", "$KV.>", "$O.>",
)

# JetStream API calls agents never need: deleting or purging a stream, or deleting single messages (DLQ discard
# and replay are operator actions in the gateway).
AGENT_DENY: tuple[str, ...] = (
    "$JS.API.STREAM.DELETE.>", "$JS.API.STREAM.PURGE.>", "$JS.API.STREAM.MSG.DELETE.>",
)


def agent_may_send(subject: str) -> bool:
    """True if an agent may publish this subject (commands: only the listed ones)."""
    if subject.startswith("cmd."):
        return subject in AGENT_COMMANDS
    return True
