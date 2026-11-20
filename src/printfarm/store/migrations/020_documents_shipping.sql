-- Wave 2 / W2-B: invoicing documents and shipments, owned by the integrations agent.
--   integrations.documents: the receipt / tax invoice-receipt of each payment, and the credit document of each
--     refund, issued by the invoicing provider (Morning). The provider numbers them and, for a tax invoice above the
--     threshold, gets the Israel Invoices allocation number itself. One document per payment (UNIQUE): a retried
--     request never issues twice. Status: pending (to issue) | issued | failed (gave up; retry by hand) | manual
--     (no automatic document for it: do it in the provider) | skipped.
--   integrations.shipments: an order's parcel: the courier (Israel Post) or a carrier typed by hand, the tracking
--     number and link, the label, and where it is (label_created -> in_transit -> delivered, or returned /
--     cancelled). The Order Pipeline shows it as Shipped's sub-state.

CREATE TABLE integrations.documents (
    id                 BIGSERIAL   PRIMARY KEY,
    connector_id       TEXT        NOT NULL REFERENCES integrations.connectors (connector_id),
    payment_id         TEXT        NOT NULL,
    order_id           TEXT,
    kind               TEXT        NOT NULL CHECK (kind IN ('receipt', 'invoice_receipt', 'credit')),
    doc_type           INT,                                     -- the provider's code (Morning: 400, 320, 330)
    amount_agorot      BIGINT      NOT NULL,                    -- gross, positive (a credit document too)
    status             TEXT        NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'issued', 'failed', 'manual', 'skipped')),
    external_id        TEXT,
    number             TEXT,
    url                TEXT,                                    -- the PDF (or, in test mode, our own preview)
    allocation         TEXT,                                    -- Israel Invoices allocation number, when given
    linked_document_id BIGINT REFERENCES integrations.documents (id),
    request            JSONB       NOT NULL DEFAULT '{}'::jsonb, -- what was asked of the provider (no secrets)
    attempts           INT         NOT NULL DEFAULT 0,
    next_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    error              TEXT,
    requested_by       TEXT        NOT NULL DEFAULT 'auto',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    issued_at          TIMESTAMPTZ,
    UNIQUE (connector_id, payment_id)
);
CREATE INDEX documents_order ON integrations.documents (order_id);
CREATE INDEX documents_due ON integrations.documents (status, next_at);

CREATE TABLE integrations.shipments (
    shipment_id     TEXT        PRIMARY KEY,
    order_id        TEXT        NOT NULL,
    connector_id    TEXT        REFERENCES integrations.connectors (connector_id),   -- NULL: a carrier typed by hand
    carrier         TEXT        NOT NULL,
    service         TEXT        NOT NULL DEFAULT '',
    tracking        TEXT        NOT NULL DEFAULT '',
    tracking_url    TEXT        NOT NULL DEFAULT '',
    label_url       TEXT        NOT NULL DEFAULT '',
    status          TEXT        NOT NULL DEFAULT 'label_created'
                    CHECK (status IN ('label_created', 'in_transit', 'delivered', 'returned', 'cancelled')),
    ship_to         JSONB       NOT NULL DEFAULT '{}'::jsonb,   -- name, street, city, zip, phone
    weight_g        INT,
    events          JSONB       NOT NULL DEFAULT '[]'::jsonb,   -- [{status, at, text, by}]
    last_checked_at TIMESTAMPTZ,
    created_by      TEXT        NOT NULL DEFAULT 'unknown',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- one live parcel per order (a cancelled label can be made again)
CREATE UNIQUE INDEX shipments_order_live ON integrations.shipments (order_id) WHERE status <> 'cancelled';
CREATE INDEX shipments_open ON integrations.shipments (status, last_checked_at) WHERE status IN ('label_created', 'in_transit');
