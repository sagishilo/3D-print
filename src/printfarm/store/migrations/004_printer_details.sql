-- 004: printer details the dashboard shows (from config/fleet*.toml, seeded by the Fleet agent).
ALTER TABLE fleet.printers ADD COLUMN model TEXT NOT NULL DEFAULT '';
ALTER TABLE fleet.printers ADD COLUMN simulated BOOLEAN NOT NULL DEFAULT false;
