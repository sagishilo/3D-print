-- 008: Finance screen (Phase 7): confirmed settings, and the filament price per gram from real purchase orders.

-- Until someone confirms them (with the accountant), the settings are the shipped placeholders.
ALTER TABLE finance.settings ADD COLUMN confirmed_by TEXT;
ALTER TABLE finance.settings ADD COLUMN confirmed_at TIMESTAMPTZ;
ALTER TABLE finance.settings ADD COLUMN updated_by TEXT;

-- Net price per gram of each filament, from the latest purchase order (evt.inventory.po.ordered); per-job filament
-- cost uses it, the settings' default only for a filament never bought through a PO.
CREATE TABLE finance.filament_prices (
    material         TEXT          NOT NULL,
    color            TEXT          NOT NULL,
    agorot_per_gram  NUMERIC(10,4) NOT NULL CHECK (agorot_per_gram > 0),
    po_id            TEXT          NOT NULL,
    at               TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (material, color)
);

CREATE INDEX IF NOT EXISTS expense_entries_product ON finance.expense_entries (product_type, entry_date) WHERE kind = 'allocated';
