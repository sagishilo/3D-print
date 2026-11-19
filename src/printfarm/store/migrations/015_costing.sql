-- Wave 1 / M5: costing. The hourly rate of post-processing labour and the fee each sales channel keeps (finance
-- settings, confirmed by the owner), and the post-processing minutes of each product (catalog, personalization agent).

ALTER TABLE finance.settings ADD COLUMN labour_agorot_per_hour NUMERIC(10,2) NOT NULL DEFAULT 4000;   -- placeholder: ₪40/h
ALTER TABLE finance.settings ADD COLUMN channel_fees JSONB NOT NULL DEFAULT '{}'::jsonb;              -- {"etsy": 9.5} (% of gross)

ALTER TABLE catalog.products ADD COLUMN post_minutes REAL NOT NULL DEFAULT 0 CHECK (post_minutes >= 0);
