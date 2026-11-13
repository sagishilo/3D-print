"""NATS subject namespace for PrintFarm Ops: one place for every subject string.

Naming: <domain>.<entity>.<event>. Events are past tense (print.job.created);
commands are imperative (print.job.create) and travel as core NATS request/reply.

CONFIRMED subjects came from the backend team. Every other name is UNCONFIRMED:
replace it with the real string from the Python backend before production.

IMPORTANT: JetStream streams below list EVENT subjects explicitly, never wildcards
such as "print.>". A stream that captures a command subject answers the request
with its own PubAck and silently breaks request/reply.
"""

# --- confirmed -------------------------------------------------------------
PRINT_JOB_CREATED = "print.job.created"            # confirmed
PRINTER_TELEMETRY = "printer.telemetry"            # confirmed (base); per-printer suffix unconfirmed

# --- events (unconfirmed) --------------------------------------------------
PRINT_JOB_SLICED = "print.job.sliced"
PRINT_JOB_STARTED = "print.job.started"
PRINT_JOB_COMPLETED = "print.job.completed"
PRINT_JOB_FAILED = "print.job.failed"
ORDER_CREATED = "order.created"
ORDER_STAGE_CHANGED = "order.stage.changed"
ORDER_MOVE_REJECTED = "order.move.rejected"
ORDER_PAYMENT_RECORDED = "order.payment.recorded"
ORDER_PAYMENT_REFUNDED = "order.payment.refunded"
PERSONALIZATION_JOB_ACCEPTED = "personalization.job.accepted"
PERSONALIZATION_JOB_COMPLETED = "personalization.job.completed"
PERSONALIZATION_JOB_FAILED = "personalization.job.failed"
SOCIAL_POST_DETECTED = "social.post.detected"
SOCIAL_DRAFT_CREATED = "social.draft.created"
SOCIAL_DRAFT_UPDATED = "social.draft.updated"
INVENTORY_SPOOL_UPDATED = "inventory.spool.updated"
INVENTORY_PO_CREATED = "inventory.po.created"
INVENTORY_PO_ORDERED = "inventory.po.ordered"
FINANCE_ENTRY_CREATED = "finance.entry.created"
FINANCE_ENTRY_ADJUSTED = "finance.entry.adjusted"
FINANCE_SUMMARY_UPDATED = "finance.summary.updated"
SYSTEM_AGENT_HEARTBEAT = "system.agent.heartbeat"
SYSTEM_CONFIG_CHANGED = "system.config.changed"

# Per-printer subjects (suffix = printer id): printer.event.<id>, camera.status.<id>
PRINTER_EVENT_PREFIX = "printer.event"
CAMERA_STATUS_PREFIX = "camera.status"
SYSTEM_ALERT_PREFIX = "system.alert"

# --- commands (request/reply, NEVER captured by a stream) ------------------
COMMANDS = {
    "drafts.approve": "social.draft.approve",
    "drafts.mark_posted": "social.draft.mark_posted",
    "orders.personalize": "personalization.job.create",
    "orders.slice": "print.job.create",
    "orders.qc_pass": "order.qc.pass",
    "orders.qc_fail": "order.qc.fail",
    "po.approve": "inventory.po.approve",
    "payments.record": "order.payment.record",
    "expenses.create": "finance.expense.create",
    "config.set": "system.config.set",
}
# printer commands: printer.cmd.<id>  {"action": "pause" | "resume" | "cancel"}
PRINTER_CMD_PREFIX = "printer.cmd"

# --- JetStream streams -------------------------------------------------------
STREAMS = {
    "EVENTS": {
        "subjects": [
            PRINT_JOB_CREATED, PRINT_JOB_SLICED, PRINT_JOB_STARTED, PRINT_JOB_COMPLETED, PRINT_JOB_FAILED,
            ORDER_CREATED, ORDER_STAGE_CHANGED, ORDER_MOVE_REJECTED, ORDER_PAYMENT_RECORDED, ORDER_PAYMENT_REFUNDED,
            PERSONALIZATION_JOB_ACCEPTED, PERSONALIZATION_JOB_COMPLETED, PERSONALIZATION_JOB_FAILED,
            SOCIAL_POST_DETECTED, SOCIAL_DRAFT_CREATED, SOCIAL_DRAFT_UPDATED,
            INVENTORY_SPOOL_UPDATED, INVENTORY_PO_CREATED, INVENTORY_PO_ORDERED,
            FINANCE_ENTRY_CREATED, FINANCE_ENTRY_ADJUSTED, FINANCE_SUMMARY_UPDATED,
            SYSTEM_CONFIG_CHANGED,
            f"{PRINTER_EVENT_PREFIX}.*", f"{SYSTEM_ALERT_PREFIX}.*",
        ],
        "max_age_s": 30 * 24 * 3600,
    },
    # high-volume, short retention
    "TELEMETRY": {
        "subjects": [PRINTER_TELEMETRY, f"{PRINTER_TELEMETRY}.*", f"{CAMERA_STATUS_PREFIX}.*", SYSTEM_AGENT_HEARTBEAT],
        "max_age_s": 3600,
    },
    # failed messages, inspected from System > DLQ
    "DLQ": {"subjects": ["dlq.>"], "max_age_s": 30 * 24 * 3600},
}

# Subjects the browser may subscribe to over /ws (prefix match). Commands never.
WS_ALLOWED_PREFIXES = (
    "print.job.", "printer.telemetry", "printer.event.", "camera.status.", "order.", "personalization.job.",
    "social.post.", "social.draft.", "inventory.", "finance.", "system.agent.", "system.config.", "system.alert.",
    "trend.", "qc.", "dlq.",
)
