-- 007: filament management (Phase 6): spool details and slots, minimum stock and reorder mode per material,
-- reservations of scheduled plates, and inventory alerts.

ALTER TABLE inventory.spools ADD COLUMN brand TEXT NOT NULL DEFAULT '';
ALTER TABLE inventory.spools ADD COLUMN slot TEXT;                 -- where on the printer: "AMS 1 · slot 2", "T0"
ALTER TABLE inventory.spools ADD COLUMN tag TEXT;                  -- QR label / RFID id (unique when set)
ALTER TABLE inventory.spools ADD COLUMN weighed_at TIMESTAMPTZ;    -- last time a person set the weight
CREATE UNIQUE INDEX spools_tag ON inventory.spools (tag) WHERE tag IS NOT NULL;
CREATE INDEX spools_printer ON inventory.spools (printer_id) WHERE status = 'loaded';

ALTER TABLE inventory.materials ADD COLUMN min_stock_grams REAL NOT NULL DEFAULT 0 CHECK (min_stock_grams >= 0);
ALTER TABLE inventory.materials ADD COLUMN reorder_mode TEXT NOT NULL DEFAULT 'auto' CHECK (reorder_mode IN ('auto', 'manual'));
ALTER TABLE inventory.materials ADD COLUMN supplier TEXT NOT NULL DEFAULT '';

-- Filament a scheduled plate will use on its printer (from evt.fleet.plate.scheduled until the plate finishes).
CREATE TABLE inventory.reservations (
    plate_id    TEXT        PRIMARY KEY,
    printer_id  TEXT        NOT NULL,
    material    TEXT        NOT NULL,
    color       TEXT        NOT NULL,
    grams       REAL        NOT NULL CHECK (grams >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX reservations_printer ON inventory.reservations (printer_id);

-- Dashboard alerts. One open alert per subject; closed when the condition clears or a person acknowledges it.
CREATE TABLE inventory.alerts (
    alert_id        TEXT        PRIMARY KEY,
    kind            TEXT        NOT NULL CHECK (kind IN ('low_stock', 'spool_short', 'spool_empty')),
    severity        TEXT        NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    subject         TEXT        NOT NULL,          -- material/color, printer or spool the alert is about
    material        TEXT        NOT NULL,
    color           TEXT        NOT NULL,
    spool_id        TEXT,
    printer_id      TEXT,
    plate_id        TEXT,
    needed_grams    REAL,
    available_grams REAL,
    message         TEXT        NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT
);
CREATE UNIQUE INDEX alerts_open ON inventory.alerts (kind, subject) WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS ledger_material_at ON inventory.ledger (material, color, at DESC);
