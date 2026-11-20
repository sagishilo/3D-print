-- Wave 2 / W2-C: production and fleet intelligence (fleet agent; camera suspicions in telemetry.failures).
--   * Operator control of the queue: hold a job, pin it to a printer, put it in a manual order (rank).
--   * Auto plate-swap: a printer with a plate changer clears its own bed after a finished plate, while plates are
--     left in its stack; then a person refills it.
--   * Maintenance: per-printer counters (print hours, plates, grams) and interval tasks with a log; an overdue task
--     may keep the printer out of the schedule.
--   * Camera suspicions: telemetry.failures gains a score (0..1) for camera detections.

ALTER TABLE fleet.jobs ADD COLUMN hold BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE fleet.jobs ADD COLUMN pinned_printer TEXT;                  -- only this printer may take it
ALTER TABLE fleet.jobs ADD COLUMN rank INT;                             -- manual order: lower first; NULL = by policy

ALTER TABLE fleet.printers ADD COLUMN auto_eject BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE fleet.printers ADD COLUMN plate_stack INT NOT NULL DEFAULT 0 CHECK (plate_stack >= 0);   -- plates it holds
ALTER TABLE fleet.printers ADD COLUMN plates_left INT NOT NULL DEFAULT 0 CHECK (plates_left >= 0);   -- still unprinted
ALTER TABLE fleet.printers ADD COLUMN print_seconds_total BIGINT NOT NULL DEFAULT 0;
ALTER TABLE fleet.printers ADD COLUMN plates_total INT NOT NULL DEFAULT 0;
ALTER TABLE fleet.printers ADD COLUMN grams_total REAL NOT NULL DEFAULT 0;

-- Counters from the history so far (finished and failed plates).
UPDATE fleet.printers p SET
    plates_total = s.n,
    print_seconds_total = s.secs,
    grams_total = s.grams
FROM (SELECT printer_id, count(*) AS n,
             coalesce(sum(extract(epoch FROM finished_at - started_at)) FILTER (WHERE started_at IS NOT NULL), 0)::bigint AS secs,
             coalesce(sum(est_grams), 0) AS grams
      FROM fleet.plates WHERE status IN ('finished', 'failed') GROUP BY printer_id) s
WHERE s.printer_id = p.printer_id;

CREATE TABLE fleet.maintenance_tasks (
    printer_id          TEXT        NOT NULL REFERENCES fleet.printers (printer_id),
    task_id             TEXT        NOT NULL CHECK (task_id ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
    label               TEXT        NOT NULL,
    every_hours         REAL        CHECK (every_hours > 0),         -- print hours
    every_plates        INT         CHECK (every_plates > 0),
    every_days          INT         CHECK (every_days > 0),
    block_when_overdue  BOOLEAN     NOT NULL DEFAULT false,
    last_done_at        TIMESTAMPTZ,
    last_done_seconds   BIGINT      NOT NULL DEFAULT 0,               -- print_seconds_total when last done
    last_done_plates    INT         NOT NULL DEFAULT 0,
    archived            BOOLEAN     NOT NULL DEFAULT false,
    PRIMARY KEY (printer_id, task_id),
    CHECK (every_hours IS NOT NULL OR every_plates IS NOT NULL OR every_days IS NOT NULL)
);

CREATE TABLE fleet.maintenance_log (
    id         BIGSERIAL   PRIMARY KEY,
    printer_id TEXT        NOT NULL,
    task_id    TEXT        NOT NULL,
    done_by    TEXT        NOT NULL,
    note       TEXT        NOT NULL DEFAULT '',
    at_seconds BIGINT      NOT NULL,
    at_plates  INT         NOT NULL,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX maintenance_log_printer ON fleet.maintenance_log (printer_id, at DESC);

ALTER TABLE telemetry.failures ADD COLUMN score REAL;                   -- camera detections: failure probability 0..1
ALTER TABLE telemetry.failures ADD COLUMN resolved_at TIMESTAMPTZ;      -- a person dismissed / handled it
ALTER TABLE telemetry.failures ADD COLUMN resolved_by TEXT;
