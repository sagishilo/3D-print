-- 003: printer control. Plate starts survive an agent restart; every pause is recorded with its verification.

-- One row per (printer, plate) start: written before the upload begins, so a restart can resume or fail it.
CREATE TABLE telemetry.plate_starts (
    printer_id    TEXT        NOT NULL,
    plate_id      TEXT        NOT NULL,
    artifact_uris JSONB       NOT NULL,
    status        TEXT        NOT NULL CHECK (status IN ('queued', 'uploading', 'starting', 'started', 'failed')),
    remote_file   TEXT,
    md5           TEXT,
    bytes         BIGINT,
    attempts      INT         NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (printer_id, plate_id)
);
CREATE INDEX plate_starts_open ON telemetry.plate_starts (status) WHERE status IN ('queued', 'uploading', 'starting');

-- Every pause request (automatic or human) and what the printer reported afterwards.
CREATE TABLE telemetry.pause_checks (
    id            BIGSERIAL   PRIMARY KEY,
    printer_id    TEXT        NOT NULL,
    plate_id      TEXT,
    reason        TEXT        NOT NULL,
    requested_by  TEXT        NOT NULL,
    confirmed     BOOLEAN     NOT NULL,
    latency_ms    INT,                                -- NULL when unconfirmed
    attempts      INT         NOT NULL,
    final_status  TEXT,
    printer_reply TEXT,
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pause_checks_printer ON telemetry.pause_checks (printer_id, checked_at DESC);
