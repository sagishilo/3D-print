-- 009: Instant Catalog (Phase 8): ready-made SKUs printed from 3MF templates (owned by the personalization agent),
-- catalog lines on orders, and sliced files printed alone on their plate.

CREATE SCHEMA IF NOT EXISTS catalog;

CREATE TABLE catalog.products (
    sku            TEXT        PRIMARY KEY,
    name           TEXT        NOT NULL,
    category       TEXT        NOT NULL,
    material       TEXT        NOT NULL,
    color          TEXT        NOT NULL,
    price_agorot   BIGINT      NOT NULL CHECK (price_agorot >= 0),      -- gross, as sold
    file_uri       TEXT        NOT NULL,                                 -- the template on the artifacts volume
    file_sha256    TEXT        NOT NULL,
    file_name      TEXT        NOT NULL DEFAULT '',                      -- as uploaded
    sliced         BOOLEAN     NOT NULL,
    printer_model  TEXT,                                                 -- the slicer's printer model id, if sliced
    footprint_w    REAL        NOT NULL CHECK (footprint_w > 0),
    footprint_d    REAL        NOT NULL CHECK (footprint_d > 0),
    est_grams      REAL        NOT NULL CHECK (est_grams > 0),
    est_minutes    REAL        NOT NULL CHECK (est_minutes > 0),
    estimated      BOOLEAN     NOT NULL,                                 -- grams/minutes typed in, not from a slicer
    active         BOOLEAN     NOT NULL DEFAULT true,
    created_by     TEXT        NOT NULL,
    updated_by     TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE coordinator.order_items ADD COLUMN sku TEXT NOT NULL DEFAULT '';
CREATE INDEX order_items_sku ON coordinator.order_items (sku) WHERE sku <> '';

ALTER TABLE fleet.jobs ADD COLUMN whole_plate BOOLEAN NOT NULL DEFAULT false;
