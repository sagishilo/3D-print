-- Wave 1 / M3: niches. Each product has one main niche (plus optional extra tags); every order line keeps the niche
-- it was sold under (a snapshot: moving a product later does not rewrite history); each payment is split across the
-- order's lines (finance.income_lines), so niche revenue adds up to the business total. Niches are owned by the
-- personalization agent (catalog schema); the reporting views are read models for the gateway.

CREATE TABLE catalog.niches (
    slug              TEXT          PRIMARY KEY CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,31}$'),
    name              TEXT          NOT NULL,
    color             INT           NOT NULL DEFAULT 1 CHECK (color BETWEEN 1 AND 8),   -- the --niche-N colour token
    icon              TEXT          NOT NULL DEFAULT 'tag',
    sort              INT           NOT NULL DEFAULT 100,
    archived          BOOLEAN       NOT NULL DEFAULT false,
    default_material  TEXT          NOT NULL DEFAULT 'PLA',
    default_colors    JSONB         NOT NULL DEFAULT '[]'::jsonb,                        -- ["white", "black"]
    target_margin_pct NUMERIC(5,2)  NOT NULL DEFAULT 50 CHECK (target_margin_pct >= 0 AND target_margin_pct < 100),
    qc_checklist      JSONB         NOT NULL DEFAULT '[]'::jsonb,                        -- ["No stringing", ...]
    packaging         JSONB         NOT NULL DEFAULT '[]'::jsonb,                        -- ["Gift box S"] (stock items: M4)
    updated_by        TEXT          NOT NULL DEFAULT 'migration',
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);

INSERT INTO catalog.niches (slug, name, color, icon, sort, default_material, default_colors, target_margin_pct, qc_checklist) VALUES
  ('home',       'Home décor',         1, 'home',   10, 'PLA',  '["white", "black"]',  55,
   '["Surface clean, no stringing", "Colour matches the order", "Stands flat, no warping"]'),
  ('kids',       'Kids & toys',        2, 'smile',  20, 'PLA',  '["red", "blue", "yellow"]', 50,
   '["No sharp edges or loose small parts", "Moving parts move freely", "Colour matches the order"]'),
  ('mechanical', 'Mechanical parts',   3, 'tool',   30, 'PETG', '["black"]',          45,
   '["Holes and fits measured", "Layer adhesion: no cracks", "Supports fully removed"]'),
  ('gifts',      'Personalised gifts', 4, 'gift',   40, 'PLA',  '["white", "black"]', 60,
   '["Text spelled exactly as ordered", "Letters crisp and readable", "No stringing"]'),
  ('unassigned', 'Unassigned',         8, 'tag',   999, 'PLA',  '[]',                 50, '[]');

ALTER TABLE catalog.products ADD COLUMN niche TEXT NOT NULL DEFAULT 'unassigned' REFERENCES catalog.niches (slug);
CREATE INDEX products_niche ON catalog.products (niche);

CREATE TABLE catalog.product_tags (
    sku   TEXT NOT NULL REFERENCES catalog.products (sku) ON DELETE CASCADE,
    niche TEXT NOT NULL REFERENCES catalog.niches (slug),
    PRIMARY KEY (sku, niche)
);

-- The niche each order line was sold under. Catalog lines from their product, nameplates as personalised gifts.
ALTER TABLE coordinator.order_items ADD COLUMN niche TEXT NOT NULL DEFAULT '';
UPDATE coordinator.order_items i SET niche = p.niche FROM catalog.products p WHERE i.sku <> '' AND i.sku = p.sku;
UPDATE coordinator.order_items SET niche = 'gifts' WHERE niche = '' AND product = 'nameplate';
UPDATE coordinator.order_items SET niche = 'unassigned' WHERE niche = '';
CREATE INDEX order_items_niche ON coordinator.order_items (niche);

-- What the inspector ticked on the niche's QC checklist: [{"item": "...", "ok": true}, ...]
ALTER TABLE coordinator.qc_checks ADD COLUMN checklist JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Each income entry split across the lines it paid for (in proportion to the line totals; the rounding remainder
-- goes to the last line). Income without an order is one line in the "unassigned" niche.
CREATE TABLE finance.income_lines (
    entry_id      BIGINT  NOT NULL REFERENCES finance.income_entries (id) ON DELETE CASCADE,
    line_no       INT     NOT NULL,
    order_id      TEXT,
    product_type  TEXT    NOT NULL,
    niche         TEXT    NOT NULL,
    net_agorot    BIGINT  NOT NULL,
    PRIMARY KEY (entry_id, line_no)
);
CREATE INDEX income_lines_niche ON finance.income_lines (niche);

-- Existing income: split the same way (weights: line totals; quantities when an order has no prices).
WITH lines AS (
    SELECT e.id AS entry_id, e.order_id, e.net_agorot AS net, i.item_id::int AS line_no,
           CASE WHEN i.product = 'catalog' AND i.sku <> '' THEN i.sku ELSE i.product END AS product_type, i.niche,
           i.line_total_agorot AS lt, i.quantity AS q
    FROM finance.income_entries e JOIN coordinator.order_items i ON i.order_id = e.order_id
), weighted AS (
    SELECT l.*, CASE WHEN sum(lt) OVER w > 0 THEN lt ELSE q END::numeric AS wt,
           CASE WHEN sum(lt) OVER w > 0 THEN sum(lt) OVER w ELSE sum(q) OVER w END::numeric AS tw,
           row_number() OVER (PARTITION BY entry_id ORDER BY line_no DESC) AS from_end
    FROM lines l WINDOW w AS (PARTITION BY entry_id)
), shares AS (
    SELECT *, round(net * wt / tw)::bigint AS share FROM weighted
)
INSERT INTO finance.income_lines (entry_id, line_no, order_id, product_type, niche, net_agorot)
SELECT entry_id, line_no, order_id, product_type, niche,
       CASE WHEN from_end = 1 THEN net - (sum(share) OVER (PARTITION BY entry_id) - share) ELSE share END
FROM shares;

INSERT INTO finance.income_lines (entry_id, line_no, order_id, product_type, niche, net_agorot)
SELECT e.id, 1, e.order_id, coalesce(e.product_type, 'other'), 'unassigned', e.net_agorot
FROM finance.income_entries e
WHERE NOT EXISTS (SELECT 1 FROM finance.income_lines l WHERE l.entry_id = e.id);

-- ------------------------------------------------------------------ reporting read models (gateway reads only)
CREATE SCHEMA IF NOT EXISTS reporting;

-- Every order line with its order's channel, stage and what Finance books it under.
CREATE VIEW reporting.order_lines_v AS
SELECT i.order_id, i.item_id, i.product, i.sku, i.niche, i.quantity, i.line_total_agorot,
       CASE WHEN i.product = 'catalog' AND i.sku <> '' THEN i.sku ELSE i.product END AS product_type,
       o.channel, o.channel = 'stock' AS stock, o.created_at, s.state
FROM coordinator.order_items i
JOIN coordinator.orders o ON o.order_id = i.order_id
JOIN coordinator.sagas s ON s.saga_id = i.order_id;

-- Per-job costs (filament, electricity) with the order line, and so the niche, they were printed for.
CREATE VIEW reporting.sku_costs_v AS
SELECT e.id AS entry_id, e.entry_date, e.category, e.net_agorot, e.job_id,
       coalesce(e.product_type, 'other') AS product_type,
       coalesce(i.niche, 'unassigned') AS niche, coalesce(o.channel = 'stock', false) AS stock
FROM finance.expense_entries e
LEFT JOIN fleet.jobs j ON j.job_id = e.job_id
LEFT JOIN coordinator.orders o ON o.order_id = j.order_id
LEFT JOIN coordinator.order_items i ON i.order_id = j.order_id
     AND i.item_id = substring(j.job_id FROM '-i([0-9]+)-u[0-9.]+-a[0-9]+$')
WHERE e.kind = 'allocated';

-- Net revenue per niche and day (sums to the income ledger).
CREATE VIEW reporting.niche_revenue_v AS
SELECT e.entry_date, l.niche, l.product_type, l.order_id, l.net_agorot
FROM finance.income_lines l JOIN finance.income_entries e ON e.id = l.entry_id;
