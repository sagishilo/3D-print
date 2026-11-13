-- 002: dashboard gateway + finance ledger schemas (formerly db/init in the ops stack), money in agorot,
-- purchase-order lifecycle, product type per job, payments register and config audit.
-- Same rule as 001: each component writes only its own schema.

-- ------------------------------------------------------------------ gateway (dashboard auth + command audit)
CREATE SCHEMA IF NOT EXISTS gateway;

CREATE TABLE gateway.refresh_tokens (
    id          BIGSERIAL   PRIMARY KEY,
    token_hash  TEXT        NOT NULL UNIQUE,          -- sha256 of the cookie value; the raw token is never stored
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE gateway.audit_log (
    id          BIGSERIAL   PRIMARY KEY,
    command_id  TEXT        NOT NULL UNIQUE,          -- = envelope id = Idempotency-Key
    actor       TEXT        NOT NULL,
    subject     TEXT        NOT NULL,
    payload     JSONB       NOT NULL,
    outcome     TEXT        NOT NULL DEFAULT 'pending', -- pending | published | duplicate | failed: <reason>
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ finance (ledger agent)
CREATE SCHEMA IF NOT EXISTS finance;

-- one row; edited from Finance > Settings (confirm the values with your accountant)
CREATE TABLE finance.settings (
    id                               INT          PRIMARY KEY CHECK (id = 1),
    business_type                    TEXT         NOT NULL DEFAULT 'murshe' CHECK (business_type IN ('patur', 'murshe')),
    vat_rate                         NUMERIC(5,4) NOT NULL DEFAULT 0.18,
    electricity_agorot_per_kwh       NUMERIC(8,2) NOT NULL DEFAULT 64,    -- placeholder: set from your bill
    default_printer_watts            NUMERIC(8,2) NOT NULL DEFAULT 150,   -- placeholder: measure per printer
    default_filament_agorot_per_gram NUMERIC(8,3) NOT NULL DEFAULT 8.5,   -- placeholder: ~85 ILS/kg
    turnover_ceiling_agorot          BIGINT,                              -- osek patur yearly ceiling
    updated_at                       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
INSERT INTO finance.settings (id) VALUES (1);

CREATE TABLE finance.income_entries (
    id               BIGSERIAL   PRIMARY KEY,
    source_event_id  TEXT        NOT NULL UNIQUE,     -- idempotency key from the bus message
    order_id         TEXT,
    entry_date       DATE        NOT NULL,
    method           TEXT        NOT NULL,            -- bit | paybox | bank_transfer | cash | card | unknown
    gross_agorot     BIGINT      NOT NULL,            -- negative for refunds
    vat_agorot       BIGINT      NOT NULL DEFAULT 0,
    net_agorot       BIGINT      NOT NULL,
    receipt_no       TEXT,                            -- from the external invoicing system
    product_type     TEXT,
    source           TEXT        NOT NULL DEFAULT 'auto' CHECK (source IN ('auto', 'manual')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX income_entries_date ON finance.income_entries (entry_date);

CREATE TABLE finance.expense_entries (
    id               BIGSERIAL   PRIMARY KEY,
    source_event_id  TEXT        NOT NULL UNIQUE,
    entry_date       DATE        NOT NULL,
    category         TEXT        NOT NULL,            -- filament | electricity | shipping | packaging | equipment | software | marketing | misc
    vendor           TEXT,
    gross_agorot     BIGINT      NOT NULL,
    vat_agorot       BIGINT      NOT NULL DEFAULT 0,
    net_agorot       BIGINT      NOT NULL,
    -- cash      = real money out (counts in P&L)
    -- estimated = placeholder until the bill arrives (counts in P&L)
    -- allocated = per-job cost for product margins only (NEVER counted in P&L: prevents double counting)
    kind             TEXT        NOT NULL CHECK (kind IN ('cash', 'estimated', 'allocated')),
    job_id           TEXT,
    product_type     TEXT,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX expense_entries_date_kind ON finance.expense_entries (entry_date, kind);

-- ------------------------------------------------------------------ coordinator: payments register + config audit
CREATE TABLE coordinator.payments (
    payment_id    TEXT        PRIMARY KEY,
    order_id      TEXT,                               -- NULL = income without an order (e.g. market stall)
    kind          TEXT        NOT NULL CHECK (kind IN ('payment', 'refund')),
    amount_agorot BIGINT      NOT NULL CHECK (amount_agorot > 0),
    method        TEXT        NOT NULL,
    at            TIMESTAMPTZ NOT NULL,
    receipt_no    TEXT,
    recorded_by   TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payments_order ON coordinator.payments (order_id);

CREATE TABLE coordinator.config_changes (
    id          BIGSERIAL   PRIMARY KEY,
    message_id  TEXT        NOT NULL UNIQUE,
    key         TEXT        NOT NULL,
    value       JSONB,
    previous    JSONB,
    changed_by  TEXT        NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ fleet: product type per job (product margins)
ALTER TABLE fleet.jobs ADD COLUMN product_type TEXT NOT NULL DEFAULT 'nameplate';

-- ------------------------------------------------------------------ inventory: money in agorot, PO lifecycle, shelves
ALTER TABLE inventory.materials RENAME COLUMN cost_per_spool TO cost_per_spool_agorot;
ALTER TABLE inventory.materials
    ALTER COLUMN cost_per_spool_agorot TYPE BIGINT USING round(cost_per_spool_agorot * 100)::bigint;
ALTER TABLE inventory.materials RENAME COLUMN auto_approve_limit TO auto_approve_limit_agorot;
ALTER TABLE inventory.materials
    ALTER COLUMN auto_approve_limit_agorot TYPE BIGINT USING round(auto_approve_limit_agorot * 100)::bigint;

-- pending_approval -> placed (approved, to be ordered) -> ordered (supplier confirmed, real total) -> received
ALTER TABLE inventory.purchase_orders RENAME COLUMN est_cost TO est_cost_agorot;
ALTER TABLE inventory.purchase_orders
    ALTER COLUMN est_cost_agorot TYPE BIGINT USING round(est_cost_agorot * 100)::bigint;
ALTER TABLE inventory.purchase_orders
    DROP CONSTRAINT purchase_orders_status_check,
    ADD CONSTRAINT purchase_orders_status_check
        CHECK (status IN ('pending_approval', 'placed', 'ordered', 'received', 'rejected')),
    ADD COLUMN supplier     TEXT,
    ADD COLUMN total_agorot BIGINT,
    ADD COLUMN vat_invoice  BOOLEAN,
    ADD COLUMN ordered_at   TIMESTAMPTZ,
    ADD COLUMN received_at  TIMESTAMPTZ;

ALTER TABLE inventory.spools ADD COLUMN location TEXT;   -- shelf, e.g. "B2", while not loaded
