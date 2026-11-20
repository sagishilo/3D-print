-- Wave 2 / W2-0: foundations, owned by the new "integrations" agent.
--   integrations.connectors / sync_state / runs: every outside service (stores, invoicing, couriers, alert
--   channels) is a connector. It is OFF and in FAKE mode until an owner switches it on; the agent polls it (outbound
--   only: nothing on the internet calls this stack) with backoff after failures.
--   integrations.mappings: which outside record is which of ours (an Etsy order is order ord-…), so a sync that runs
--   twice never creates twice.
--   notify.outbox: every alert of the platform in one place, with its delivery state; W2-D delivers it to
--   Telegram / e-mail / WhatsApp with quiet hours. Until then it is shown on the dashboard.

CREATE SCHEMA IF NOT EXISTS integrations;

CREATE TABLE integrations.connectors (
    connector_id   TEXT        PRIMARY KEY CHECK (connector_id ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
    kind           TEXT        NOT NULL,                 -- store | invoicing | courier | alerts | demo
    label          TEXT        NOT NULL,
    enabled        BOOLEAN     NOT NULL DEFAULT false,
    mode           TEXT        NOT NULL DEFAULT 'fake' CHECK (mode IN ('fake', 'live')),
    interval_s     INT         NOT NULL DEFAULT 300 CHECK (interval_s BETWEEN 30 AND 86400),
    settings       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    secrets_status JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- {"KEY": true|false}: set or missing, never a value
    updated_by     TEXT        NOT NULL DEFAULT 'agent',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE integrations.sync_state (
    connector_id         TEXT        PRIMARY KEY REFERENCES integrations.connectors (connector_id),
    last_run_at          TIMESTAMPTZ,
    last_ok_at           TIMESTAMPTZ,
    last_error           TEXT,
    last_error_at        TIMESTAMPTZ,
    consecutive_failures INT         NOT NULL DEFAULT 0,
    next_run_at          TIMESTAMPTZ,
    cursor               JSONB       NOT NULL DEFAULT '{}'::jsonb,   -- where the last sync stopped (since, page)
    runs                 INT         NOT NULL DEFAULT 0,
    items                BIGINT      NOT NULL DEFAULT 0
);

CREATE TABLE integrations.runs (
    id           BIGSERIAL   PRIMARY KEY,
    connector_id TEXT        NOT NULL REFERENCES integrations.connectors (connector_id),
    trigger      TEXT        NOT NULL DEFAULT 'schedule',   -- schedule | manual
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    ok           BOOLEAN,
    items        INT         NOT NULL DEFAULT 0,
    created      INT         NOT NULL DEFAULT 0,
    updated      INT         NOT NULL DEFAULT 0,
    error        TEXT
);
CREATE INDEX runs_connector_at ON integrations.runs (connector_id, started_at DESC);

CREATE TABLE integrations.mappings (
    connector_id TEXT        NOT NULL REFERENCES integrations.connectors (connector_id),
    entity       TEXT        NOT NULL,                       -- order | product | customer | shipment | document …
    external_id  TEXT        NOT NULL,
    internal_id  TEXT        NOT NULL,
    payload_hash TEXT        NOT NULL DEFAULT '',            -- unchanged outside record: nothing to update
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (connector_id, entity, external_id),
    UNIQUE (connector_id, entity, internal_id)
);

CREATE SCHEMA IF NOT EXISTS notify;

CREATE TABLE notify.outbox (
    id           BIGSERIAL   PRIMARY KEY,
    topic        TEXT        NOT NULL,                       -- safety | print_failed | order_attention | stock | integration | …
    severity     TEXT        NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    title        TEXT        NOT NULL,
    body         TEXT        NOT NULL DEFAULT '',
    link         TEXT        NOT NULL DEFAULT '',            -- a dashboard path, e.g. /orders?order=…
    dedupe_key   TEXT,                                       -- the same open problem notifies once
    min_role     TEXT        NOT NULL DEFAULT 'operator',
    status       TEXT        NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'sent', 'held', 'failed', 'skipped', 'dashboard')),
    channels     JSONB       NOT NULL DEFAULT '[]'::jsonb,   -- where it went (W2-D): ["telegram", "email"]
    attempts     INT         NOT NULL DEFAULT 0,
    error        TEXT,
    source_event TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX outbox_dedupe ON notify.outbox (dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX outbox_created ON notify.outbox (created_at DESC);
