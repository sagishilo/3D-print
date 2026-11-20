-- Wave 2 / W2-A: store connectors (Shopify first), owned by the integrations agent.
--   integrations.sku_map: which of our products a store's SKU is (a catalog SKU, a nameplate in a filament with
--     its text from a line property, or a line to ignore). A store SKU equal to an active catalog SKU needs none.
--   integrations.store_orders: every order the store showed us and what became of it: waiting for a mapping or for
--     payment, imported as order <order_id>, cancelled at the store, or skipped by a person. The "map this" queue
--     is read from here.
--   integrations.pushes: what we send back to a store (the tracking of a shipped order), retried with backoff.

CREATE TABLE integrations.sku_map (
    connector_id  TEXT        NOT NULL REFERENCES integrations.connectors (connector_id),
    external_key  TEXT        NOT NULL,             -- the store's SKU, or "title:<product title>" when it has none
    product       TEXT        NOT NULL CHECK (product IN ('catalog', 'nameplate', 'ignore')),
    sku           TEXT        NOT NULL DEFAULT '',
    material      TEXT        NOT NULL DEFAULT 'PLA',
    color         TEXT        NOT NULL DEFAULT 'black',
    text_property TEXT        NOT NULL DEFAULT 'Name',
    updated_by    TEXT        NOT NULL DEFAULT 'agent',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (connector_id, external_key)
);

CREATE TABLE integrations.store_orders (
    connector_id     TEXT        NOT NULL REFERENCES integrations.connectors (connector_id),
    external_id      TEXT        NOT NULL,
    name             TEXT        NOT NULL DEFAULT '',          -- the store's order number, e.g. #1001
    customer         TEXT        NOT NULL DEFAULT '',
    total_agorot     BIGINT      NOT NULL DEFAULT 0,
    currency         TEXT        NOT NULL DEFAULT 'ILS',
    financial_status TEXT        NOT NULL DEFAULT '',
    placed_at        TIMESTAMPTZ,
    status           TEXT        NOT NULL CHECK (status IN ('needs_mapping', 'waiting_payment', 'imported', 'cancelled',
                                                            'skipped')),
    order_id         TEXT,                                      -- ours, once imported
    problems         JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- [{kind: unmapped|problem, key, title, message}]
    payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- the order as the store showed it (normalised)
    seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    imported_at      TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (connector_id, external_id)
);
CREATE INDEX store_orders_status ON integrations.store_orders (status, placed_at DESC);
CREATE INDEX store_orders_order ON integrations.store_orders (order_id) WHERE order_id IS NOT NULL;

CREATE TABLE integrations.pushes (
    id           BIGSERIAL   PRIMARY KEY,
    connector_id TEXT        NOT NULL REFERENCES integrations.connectors (connector_id),
    kind         TEXT        NOT NULL,                          -- tracking
    external_id  TEXT        NOT NULL,                          -- the store's order
    order_id     TEXT        NOT NULL,                          -- ours
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status       TEXT        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    attempts     INT         NOT NULL DEFAULT 0,
    next_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at      TIMESTAMPTZ,
    UNIQUE (connector_id, kind, external_id)
);
CREATE INDEX pushes_due ON integrations.pushes (status, next_at);
