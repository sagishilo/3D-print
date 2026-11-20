-- Phase 9 preparation: real printers. What a printer can take (build height, AMS slots and what is loaded in them),
-- what a job needs (the printer model it was sliced for, how many filaments, its height), and the wear a printer
-- brings with it (print hours before this platform).

ALTER TABLE fleet.printers
    ADD COLUMN max_z       REAL    NOT NULL DEFAULT 0,          -- build height in mm; 0 = not checked
    ADD COLUMN ams_slots   INTEGER NOT NULL DEFAULT 0 CHECK (ams_slots BETWEEN 0 AND 16),
    ADD COLUMN loaded      JSONB   NOT NULL DEFAULT '[]',       -- [["PLA","black"], ...]: the AMS slots, in order
    ADD COLUMN seed_hours  REAL    NOT NULL DEFAULT 0;          -- initial_print_hours already counted in

ALTER TABLE fleet.jobs
    ADD COLUMN printer_model  TEXT,                              -- the slicer's printer_model_id (sliced files)
    ADD COLUMN filament_count INTEGER NOT NULL DEFAULT 1 CHECK (filament_count BETWEEN 1 AND 16),
    ADD COLUMN max_z_mm       REAL;                              -- height of the sliced plate, when known

ALTER TABLE catalog.products
    ADD COLUMN filament_count INTEGER NOT NULL DEFAULT 1 CHECK (filament_count BETWEEN 1 AND 16),
    ADD COLUMN max_z_mm       REAL;
