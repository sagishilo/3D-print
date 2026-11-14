-- 006: orders v2 (Phase 5): customers, channels and priced line items; one nameplate per order line.

CREATE TABLE coordinator.orders (
    order_id         TEXT        PRIMARY KEY,
    customer_name    TEXT        NOT NULL,
    customer_ref     TEXT        NOT NULL DEFAULT '',
    channel          TEXT        NOT NULL,
    due_by           TIMESTAMPTZ,
    notes            TEXT        NOT NULL DEFAULT '',
    subtotal_agorot  BIGINT      NOT NULL CHECK (subtotal_agorot >= 0),
    discount_agorot  BIGINT      NOT NULL DEFAULT 0 CHECK (discount_agorot >= 0),
    shipping_agorot  BIGINT      NOT NULL DEFAULT 0 CHECK (shipping_agorot >= 0),
    total_agorot     BIGINT      NOT NULL CHECK (total_agorot >= 0),     -- gross, VAT included
    units            INT         NOT NULL CHECK (units > 0),
    created_by       TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX orders_created ON coordinator.orders (created_at DESC);

CREATE TABLE coordinator.order_items (
    order_id          TEXT    NOT NULL REFERENCES coordinator.orders (order_id),
    item_id           TEXT    NOT NULL,                  -- "1", "2", ... (line number)
    product           TEXT    NOT NULL,
    text              TEXT    NOT NULL DEFAULT '',
    material          TEXT    NOT NULL,
    color             TEXT    NOT NULL,
    quantity          INT     NOT NULL CHECK (quantity > 0),
    unit_price_agorot BIGINT  NOT NULL CHECK (unit_price_agorot >= 0),
    line_total_agorot BIGINT  NOT NULL CHECK (line_total_agorot >= 0),
    PRIMARY KEY (order_id, item_id)
);

-- v1 orders already in the saga table become one-line orders (price unknown), so the board shows their history.
INSERT INTO coordinator.orders (order_id, customer_name, channel, due_by, subtotal_agorot, total_agorot, units, created_by, created_at)
SELECT saga_id, coalesce(context -> 'order' ->> 'customer_ref', ''), 'api', deadline_at, 0, 0,
       greatest(coalesce((context -> 'order' ->> 'quantity')::int, 1), 1), 'migration', created_at
FROM coordinator.sagas WHERE saga_type = 'order' AND NOT (context ? 'items')
ON CONFLICT DO NOTHING;
INSERT INTO coordinator.order_items (order_id, item_id, product, text, material, color, quantity, unit_price_agorot, line_total_agorot)
SELECT saga_id, '1', coalesce(context -> 'order' ->> 'product', 'nameplate'), coalesce(context -> 'order' ->> 'text', ''),
       coalesce(context -> 'order' ->> 'material', 'PLA'), coalesce(context -> 'order' ->> 'color', 'black'),
       greatest(coalesce((context -> 'order' ->> 'quantity')::int, 1), 1), 0, 0
FROM coordinator.sagas WHERE saga_type = 'order' AND NOT (context ? 'items')
ON CONFLICT DO NOTHING;

-- v1 saga states: a finished order is shipped; an open v1 saga cannot continue under the v2 state machine.
UPDATE coordinator.sagas SET state = 'SHIPPED' WHERE saga_type = 'order' AND state = 'COMPLETED';
UPDATE coordinator.sagas SET state = 'CANCELLED',
       context = context || '{"cancelled": {"reason": "orders v1 saga closed by migration 006", "by": "migration"}}'
WHERE saga_type = 'order' AND NOT (context ? 'items') AND state NOT IN ('SHIPPED', 'CANCELLED');
DROP INDEX IF EXISTS coordinator.sagas_open;
CREATE INDEX sagas_open ON coordinator.sagas (state) WHERE state NOT IN ('SHIPPED', 'CANCELLED');
CREATE INDEX IF NOT EXISTS saga_history_saga ON coordinator.saga_history (saga_id, at DESC);

-- personalization: one artifact per order line
ALTER TABLE personalization.artifacts ADD COLUMN item_id TEXT NOT NULL DEFAULT '';
ALTER TABLE personalization.artifacts DROP CONSTRAINT artifacts_pkey;
ALTER TABLE personalization.artifacts ADD PRIMARY KEY (order_id, item_id);

-- Every quality check (dashboard KPI "QC fails · 7 d"), written by the coordinator with evt.order.qc.recorded.
CREATE TABLE coordinator.qc_checks (
    id            BIGSERIAL   PRIMARY KEY,
    order_id      TEXT        NOT NULL,
    passed        BOOLEAN     NOT NULL,
    units_failed  INT         NOT NULL DEFAULT 0,
    units_checked INT         NOT NULL,
    reason        TEXT        NOT NULL DEFAULT '',
    checked_by    TEXT        NOT NULL,
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX qc_checks_at ON coordinator.qc_checks (checked_at DESC);
