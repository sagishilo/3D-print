-- One schema per agent: an agent reads and writes ONLY its own schema plus
-- messaging.*. Cross-agent data travels as events, never as joins.
-- In production each agent connects with its own role that has privileges on
-- its schema only (see docs/architecture/04-state-management.md).

-- ------------------------------------------------------------------ messaging
CREATE SCHEMA IF NOT EXISTS messaging;

-- Transactional outbox: written in the same transaction as the business change,
-- relayed to NATS by the producing agent. published_at NULL = not yet relayed.
CREATE TABLE messaging.outbox (
    seq           BIGSERIAL PRIMARY KEY,
    producer      TEXT        NOT NULL,
    message_id    TEXT        NOT NULL UNIQUE,
    subject       TEXT        NOT NULL,
    envelope      JSONB       NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at  TIMESTAMPTZ
);
CREATE INDEX outbox_pending ON messaging.outbox (producer, seq) WHERE published_at IS NULL;

-- Inbox: one row per (consumer, message) that was processed. Inserted in the
-- same transaction as the handler's effects => a redelivered message is a no-op.
CREATE TABLE messaging.inbox (
    consumer      TEXT        NOT NULL,
    message_id    TEXT        NOT NULL,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, message_id)
);

-- ------------------------------------------------------------------ coordinator
CREATE SCHEMA IF NOT EXISTS coordinator;

CREATE TABLE coordinator.sagas (
    saga_id     TEXT PRIMARY KEY,            -- = order id = correlation id
    saga_type   TEXT        NOT NULL,
    state       TEXT        NOT NULL,
    context     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    version     INT         NOT NULL DEFAULT 0,  -- optimistic concurrency
    deadline_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sagas_open ON coordinator.sagas (state) WHERE state NOT IN ('COMPLETED', 'CANCELLED');

CREATE TABLE coordinator.saga_history (
    id           BIGSERIAL PRIMARY KEY,
    saga_id      TEXT NOT NULL REFERENCES coordinator.sagas (saga_id),
    from_state   TEXT,
    to_state     TEXT NOT NULL,
    message_id   TEXT NOT NULL,
    message_type TEXT NOT NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE coordinator.approvals (
    approval_id  TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    summary      TEXT NOT NULL,
    details      JSONB NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by   TEXT,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at   TIMESTAMPTZ
);

-- ------------------------------------------------------------------ personalization
CREATE SCHEMA IF NOT EXISTS personalization;

CREATE TABLE personalization.artifacts (
    order_id      TEXT PRIMARY KEY,
    text          TEXT    NOT NULL,
    source_uri    TEXT    NOT NULL,
    artifact_uri  TEXT    NOT NULL,
    footprint_w   REAL    NOT NULL,
    footprint_d   REAL    NOT NULL,
    est_grams     REAL    NOT NULL,
    est_minutes   REAL    NOT NULL,
    sliced        BOOLEAN NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ fleet
CREATE SCHEMA IF NOT EXISTS fleet;

CREATE TABLE fleet.printers (
    printer_id  TEXT PRIMARY KEY,
    vendor      TEXT    NOT NULL,
    bed_w       REAL    NOT NULL,
    bed_d       REAL    NOT NULL,
    nozzle_mm   REAL    NOT NULL,
    material    TEXT    NOT NULL,
    color       TEXT    NOT NULL,
    online      BOOLEAN NOT NULL DEFAULT false,
    busy        BOOLEAN NOT NULL DEFAULT false,
    bed_clear   BOOLEAN NOT NULL DEFAULT true,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE fleet.plates (
    plate_id    TEXT PRIMARY KEY,
    printer_id  TEXT NOT NULL REFERENCES fleet.printers (printer_id),
    material    TEXT NOT NULL,
    color       TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('scheduled', 'printing', 'finished', 'failed')),
    est_grams   REAL NOT NULL,
    est_minutes REAL NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE fleet.jobs (
    job_id        TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL,
    correlationid TEXT NOT NULL,
    artifact_uri  TEXT NOT NULL,
    material      TEXT NOT NULL,
    color         TEXT NOT NULL,
    footprint_w   REAL NOT NULL,
    footprint_d   REAL NOT NULL,
    est_grams     REAL NOT NULL,
    est_minutes   REAL NOT NULL,
    due_by        TIMESTAMPTZ,
    priority      INT  NOT NULL DEFAULT 0,
    status        TEXT NOT NULL CHECK (status IN ('queued', 'scheduled', 'printing', 'completed', 'failed', 'cancelled')),
    plate_id      TEXT REFERENCES fleet.plates (plate_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_queued ON fleet.jobs (material, color, priority DESC, due_by) WHERE status = 'queued';

CREATE TABLE fleet.plate_items (
    plate_id  TEXT NOT NULL REFERENCES fleet.plates (plate_id),
    job_id    TEXT NOT NULL REFERENCES fleet.jobs (job_id),
    x         REAL NOT NULL,
    y         REAL NOT NULL,
    w         REAL NOT NULL,
    d         REAL NOT NULL,
    rotated   BOOLEAN NOT NULL,
    PRIMARY KEY (plate_id, job_id)
);

-- ------------------------------------------------------------------ telemetry
CREATE SCHEMA IF NOT EXISTS telemetry;

-- One row per print run; the PK makes lifecycle events idempotent across restarts.
CREATE TABLE telemetry.print_sessions (
    printer_id  TEXT NOT NULL,
    plate_id    TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    succeeded   BOOLEAN,
    PRIMARY KEY (printer_id, plate_id)
);

CREATE TABLE telemetry.failures (
    id           BIGSERIAL PRIMARY KEY,
    printer_id   TEXT NOT NULL,
    plate_id     TEXT,
    detector     TEXT NOT NULL,
    severity     TEXT NOT NULL,
    message      TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ inventory
CREATE SCHEMA IF NOT EXISTS inventory;

CREATE TABLE inventory.materials (
    material             TEXT NOT NULL,
    color                TEXT NOT NULL,
    grams_per_spool      REAL NOT NULL,
    cost_per_spool       REAL NOT NULL,
    lead_time_days       REAL NOT NULL,
    safety_stock_days    REAL NOT NULL,
    review_period_days   REAL NOT NULL DEFAULT 14,
    baseline_daily_grams REAL NOT NULL,
    auto_approve_limit   REAL NOT NULL,
    PRIMARY KEY (material, color)
);

CREATE TABLE inventory.spools (
    spool_id        TEXT PRIMARY KEY,
    material        TEXT NOT NULL,
    color           TEXT NOT NULL,
    initial_grams   REAL NOT NULL,
    remaining_grams REAL NOT NULL,
    printer_id      TEXT,                        -- set when loaded on a printer
    status          TEXT NOT NULL CHECK (status IN ('stock', 'loaded', 'empty')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only consumption ledger; spools.remaining_grams is its running total.
CREATE TABLE inventory.ledger (
    id          BIGSERIAL PRIMARY KEY,
    spool_id    TEXT REFERENCES inventory.spools (spool_id),
    material    TEXT NOT NULL,
    color       TEXT NOT NULL,
    delta_grams REAL NOT NULL,
    reason      TEXT NOT NULL,
    ref         TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE inventory.purchase_orders (
    po_id       TEXT PRIMARY KEY,
    material    TEXT NOT NULL,
    color       TEXT NOT NULL,
    spools      INT  NOT NULL,
    est_cost    REAL NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending_approval', 'placed', 'received', 'rejected')),
    approval_id TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ social / trend (LLM agents, phase 3)
CREATE SCHEMA IF NOT EXISTS social;

CREATE TABLE social.captures (
    capture_id  TEXT PRIMARY KEY,
    group_name  TEXT NOT NULL,
    post_url    TEXT NOT NULL,
    post_text   TEXT NOT NULL,
    captured_by TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE social.leads (
    lead_id      TEXT PRIMARY KEY,
    capture_id   TEXT NOT NULL REFERENCES social.captures (capture_id),
    intent_score REAL NOT NULL,
    product      TEXT NOT NULL,
    summary      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reply_drafted', 'replied', 'won', 'lost', 'ignored')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE SCHEMA IF NOT EXISTS trend;

CREATE TABLE trend.signals (
    signal_id  TEXT PRIMARY KEY,
    topic      TEXT NOT NULL,
    direction  TEXT NOT NULL,
    strength   REAL NOT NULL,
    evidence   JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
