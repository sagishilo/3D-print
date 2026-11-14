-- Phase 4: calibration runs (telemetry agent) and the fleet's view of a calibrating printer.

-- One row per calibration request; calibration_id = the command's envelope id (redeliveries are ignored).
CREATE TABLE telemetry.calibrations (
    calibration_id TEXT        PRIMARY KEY,
    printer_id     TEXT        NOT NULL,
    routine        TEXT        NOT NULL,
    temperature    INT,
    requested_by   TEXT        NOT NULL,
    status         TEXT        NOT NULL CHECK (status IN ('running', 'done', 'failed', 'refused')),
    detail         TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ
);
CREATE INDEX calibrations_printer ON telemetry.calibrations (printer_id, started_at DESC);
CREATE INDEX calibrations_running ON telemetry.calibrations (status) WHERE status = 'running';

-- The scheduler leaves a calibrating printer alone (set and cleared from evt.printer.calibration.*).
ALTER TABLE fleet.printers ADD COLUMN calibrating BOOLEAN NOT NULL DEFAULT false;

-- Fast per-printer history for the dashboard's printer details.
CREATE INDEX IF NOT EXISTS failures_printer ON telemetry.failures (printer_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS print_sessions_recent ON telemetry.print_sessions (printer_id, started_at DESC);
