-- Wave 1 / M0: which units failed a QC check (item.unit keys, e.g. ["1.2", "2.1"]), so a QC failure can be traced
-- back to its order line and SKU (the niche scorecard reads it). Older checks keep only their counts.
ALTER TABLE coordinator.qc_checks ADD COLUMN IF NOT EXISTS failed_keys jsonb NOT NULL DEFAULT '[]'::jsonb;
