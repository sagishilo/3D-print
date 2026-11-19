-- Wave 1 / M6: daily analytics and the Trend Analyst (agent "trend", owner of these tables).
--   reporting.daily_sku / daily_niche: one row per business day (Asia/Jerusalem) and product (or niche), rebuilt
--   idempotently each night (and on demand: cmd.trend.backfill) from the ledgers and the read models of M3-M5.
--   trend.signals: what is rising or falling (units sold, social intent), per day; trend.insights: the optional
--   nightly Hebrew insight per niche (one cheap Claude call, behind the trend.enabled setting).

CREATE TABLE reporting.daily_sku (
    day             DATE          NOT NULL,
    sku             TEXT          NOT NULL,          -- what Finance books under: the SKU, or "nameplate", "other"
    niche           TEXT          NOT NULL,
    units_sold      INT           NOT NULL DEFAULT 0,  -- shipped to customers (not to the shelf)
    revenue_agorot  BIGINT        NOT NULL DEFAULT 0,  -- net, from the income lines
    cost_agorot     BIGINT        NOT NULL DEFAULT 0,  -- per-job filament + power (not stock builds) + pieces sold from stock
    print_hours     NUMERIC(10,2) NOT NULL DEFAULT 0,  -- jobs completed that day
    qc_checked      INT           NOT NULL DEFAULT 0,
    qc_failed       INT           NOT NULL DEFAULT 0,
    captures        INT           NOT NULL DEFAULT 0,  -- social posts matched to the SKU
    leads           INT           NOT NULL DEFAULT 0,
    posted          INT           NOT NULL DEFAULT 0,  -- replies posted
    PRIMARY KEY (day, sku, niche)
);
CREATE INDEX daily_sku_niche ON reporting.daily_sku (niche, day);

CREATE TABLE reporting.daily_niche (
    day             DATE          NOT NULL,
    niche           TEXT          NOT NULL,
    units_sold      INT           NOT NULL DEFAULT 0,
    revenue_agorot  BIGINT        NOT NULL DEFAULT 0,
    cost_agorot     BIGINT        NOT NULL DEFAULT 0,
    print_hours     NUMERIC(10,2) NOT NULL DEFAULT 0,
    qc_checked      INT           NOT NULL DEFAULT 0,
    qc_failed       INT           NOT NULL DEFAULT 0,
    captures        INT           NOT NULL DEFAULT 0,
    leads           INT           NOT NULL DEFAULT 0,
    posted          INT           NOT NULL DEFAULT 0,
    PRIMARY KEY (day, niche)
);

CREATE TABLE reporting.rollups (
    day         DATE        PRIMARY KEY,
    rows        INT         NOT NULL,
    rolled_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Signals of a day: one per subject (a niche or a SKU) and day, so re-running the night changes nothing.
ALTER TABLE trend.signals ADD COLUMN day DATE;
ALTER TABLE trend.signals ADD COLUMN scope TEXT;          -- niche | sku
ALTER TABLE trend.signals ADD COLUMN subject TEXT;        -- the niche slug or the SKU
ALTER TABLE trend.signals ADD COLUMN niche TEXT;
ALTER TABLE trend.signals ADD COLUMN change_pct REAL;
CREATE INDEX signals_day ON trend.signals (day DESC, scope);

CREATE TABLE trend.insights (
    day         DATE          NOT NULL,
    niche       TEXT          NOT NULL,
    text        TEXT          NOT NULL,
    model       TEXT          NOT NULL,
    cost_usd    NUMERIC(10,6) NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (day, niche)
);
