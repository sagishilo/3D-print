-- PrintFarm Ops: initial schema for the gateway (auth, audit) and the Ledger service.
-- Runs automatically ONLY when the Postgres data volume is empty (first start).
-- Later changes go in a migration tool (e.g. Alembic), not in this file.

CREATE TABLE auth_refresh_tokens (
    id          bigserial PRIMARY KEY,
    token_hash  text        NOT NULL UNIQUE,          -- sha256 of the cookie value; raw token never stored
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz
);

CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    command_id  text        NOT NULL UNIQUE,
    actor       text        NOT NULL,
    subject     text        NOT NULL,
    payload     jsonb       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- one row; edited from Finance > Settings (confirm values with your accountant)
CREATE TABLE finance_settings (
    id                               int PRIMARY KEY CHECK (id = 1),
    business_type                    text    NOT NULL DEFAULT 'murshe' CHECK (business_type IN ('patur', 'murshe')),
    vat_rate                         numeric(5,4) NOT NULL DEFAULT 0.18,
    electricity_agorot_per_kwh       numeric(8,2) NOT NULL DEFAULT 64,     -- placeholder: set from your bill
    default_printer_watts            numeric(8,2) NOT NULL DEFAULT 150,    -- placeholder: measure per printer
    default_filament_agorot_per_gram numeric(8,3) NOT NULL DEFAULT 8.5,    -- placeholder: ~₪85/kg
    turnover_ceiling_agorot          bigint,                               -- osek patur yearly ceiling
    updated_at                       timestamptz NOT NULL DEFAULT now()
);
INSERT INTO finance_settings (id) VALUES (1);

CREATE TABLE income_entries (
    id               bigserial PRIMARY KEY,
    source_event_id  text        NOT NULL UNIQUE,     -- idempotency key from the NATS event
    order_id         text,
    entry_date       date        NOT NULL,
    method           text        NOT NULL,            -- bit | paybox | bank_transfer | cash | unknown
    gross_agorot     bigint      NOT NULL,            -- negative for refunds
    vat_agorot       bigint      NOT NULL DEFAULT 0,
    net_agorot       bigint      NOT NULL,
    receipt_no       text,                            -- from the external invoicing system
    product_type     text,
    source           text        NOT NULL DEFAULT 'auto' CHECK (source IN ('auto', 'manual')),
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON income_entries (entry_date);

CREATE TABLE expense_entries (
    id               bigserial PRIMARY KEY,
    source_event_id  text        NOT NULL UNIQUE,
    entry_date       date        NOT NULL,
    category         text        NOT NULL,            -- filament | electricity | shipping | packaging | equipment | software | marketing | misc
    vendor           text,
    gross_agorot     bigint      NOT NULL,
    vat_agorot       bigint      NOT NULL DEFAULT 0,
    net_agorot       bigint      NOT NULL,
    -- cash = real money out (counts in P&L); estimated = placeholder until the bill arrives (counts in P&L);
    -- allocated = per-job cost for product margins only (NEVER counted in P&L: prevents double counting)
    kind             text        NOT NULL CHECK (kind IN ('cash', 'estimated', 'allocated')),
    job_id           text,
    product_type     text,
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON expense_entries (entry_date, kind);
