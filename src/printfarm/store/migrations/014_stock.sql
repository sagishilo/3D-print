-- Wave 1 / M4: stock beyond filament.
--   * Finished goods on the shelf (owned by the coordinator, which reserves them for new orders in the same
--     transaction that creates the order): a stock batch shipped "to the shelf" adds units; an order line of a
--     stocked SKU takes units from the shelf first (those units are not printed); shipping the order consumes them.
--   * Parts, packaging and consumables with a bill of materials per SKU (owned by the inventory agent): deducted
--     when units are made (parts) and when orders ship (packaging); reordered through the purchase-order flow.
--   * Filament condition: when a spool was opened and last dried.

-- ------------------------------------------------------------------ finished goods (coordinator)
CREATE TABLE coordinator.finished_goods (
    sku        TEXT        PRIMARY KEY,
    on_hand    INT         NOT NULL DEFAULT 0 CHECK (on_hand >= 0),
    reserved   INT         NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    min_stock  INT         NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
    location   TEXT        NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (reserved <= on_hand)
);

-- Every movement: built (+on_hand, from a stock batch), reserved / released (reserved), sold (-on_hand -reserved),
-- counted (a stock count corrected on_hand).
CREATE TABLE coordinator.fg_ledger (
    id             BIGSERIAL   PRIMARY KEY,
    sku            TEXT        NOT NULL,
    delta_on_hand  INT         NOT NULL DEFAULT 0,
    delta_reserved INT         NOT NULL DEFAULT 0,
    reason         TEXT        NOT NULL CHECK (reason IN ('built', 'reserved', 'released', 'sold', 'counted')),
    order_id       TEXT,
    by             TEXT        NOT NULL DEFAULT 'auto',
    note           TEXT        NOT NULL DEFAULT '',
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX fg_ledger_sku_at ON coordinator.fg_ledger (sku, at DESC);

-- ------------------------------------------------------------------ parts, packaging, consumables (inventory)
CREATE TABLE inventory.items (
    item_id      TEXT        PRIMARY KEY CHECK (item_id ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
    name         TEXT        NOT NULL,
    kind         TEXT        NOT NULL CHECK (kind IN ('part', 'packaging', 'consumable')),
    unit         TEXT        NOT NULL DEFAULT 'pcs',
    on_hand      NUMERIC(12,2) NOT NULL DEFAULT 0,
    min_qty      NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (min_qty >= 0),
    reorder_qty  NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (reorder_qty >= 0),
    supplier     TEXT        NOT NULL DEFAULT '',
    lead_days    INT         NOT NULL DEFAULT 7 CHECK (lead_days >= 0),
    cost_agorot  BIGINT      NOT NULL DEFAULT 0 CHECK (cost_agorot >= 0),   -- net price of one unit
    archived     BOOLEAN     NOT NULL DEFAULT false,
    updated_by   TEXT        NOT NULL DEFAULT 'migration',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE inventory.item_ledger (
    id       BIGSERIAL     PRIMARY KEY,
    item_id  TEXT          NOT NULL REFERENCES inventory.items (item_id),
    delta    NUMERIC(12,2) NOT NULL,
    reason   TEXT          NOT NULL,              -- made | shipped | received | counted | registered
    ref      TEXT,
    at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX item_ledger_item_at ON inventory.item_ledger (item_id, at DESC);

-- What one finished piece of a SKU takes besides filament.
CREATE TABLE inventory.bom (
    sku       TEXT          NOT NULL,
    item_id   TEXT          NOT NULL REFERENCES inventory.items (item_id),
    qty       NUMERIC(10,3) NOT NULL CHECK (qty > 0),
    PRIMARY KEY (sku, item_id)
);

-- Purchase orders for items reuse the filament PO flow (approval, ordered, received).
ALTER TABLE inventory.purchase_orders ADD COLUMN item_id TEXT REFERENCES inventory.items (item_id);
ALTER TABLE inventory.purchase_orders ADD COLUMN quantity NUMERIC(12,2);

ALTER TABLE inventory.alerts DROP CONSTRAINT alerts_kind_check;
ALTER TABLE inventory.alerts ADD CONSTRAINT alerts_kind_check
    CHECK (kind IN ('low_stock', 'spool_short', 'spool_empty', 'low_item'));

-- ------------------------------------------------------------------ filament condition
ALTER TABLE inventory.spools ADD COLUMN opened_at TIMESTAMPTZ;      -- first loaded on a printer
ALTER TABLE inventory.spools ADD COLUMN dried_at  TIMESTAMPTZ;      -- last "Mark dried"
UPDATE inventory.spools SET opened_at = created_at WHERE status = 'loaded' OR remaining_grams < initial_grams;

-- ------------------------------------------------------------------ read models
-- Units sold from the shelf, with the average cost of building one (stock-batch filament and electricity per unit
-- built): the cost of goods sold for a sale from stock, which the margin reports add to the selling line's SKU.
CREATE VIEW reporting.stock_sales_v AS
WITH built AS (
    SELECT sku, sum(delta_on_hand) AS units FROM coordinator.fg_ledger WHERE reason = 'built' GROUP BY sku
), cost AS (
    SELECT product_type AS sku, sum(net_agorot) AS agorot FROM reporting.sku_costs_v WHERE stock GROUP BY 1
)
SELECT l.at, (l.at AT TIME ZONE 'Asia/Jerusalem')::date AS entry_date, l.sku, l.order_id,
       coalesce((SELECT i.niche FROM coordinator.order_items i WHERE i.order_id = l.order_id AND i.sku = l.sku LIMIT 1),
                'unassigned') AS niche,
       -l.delta_on_hand AS units,
       CASE WHEN b.units > 0 THEN round(-l.delta_on_hand * coalesce(c.agorot, 0)::numeric / b.units)::bigint ELSE 0 END
         AS cost_agorot
FROM coordinator.fg_ledger l
LEFT JOIN built b ON b.sku = l.sku
LEFT JOIN cost c ON c.sku = l.sku
WHERE l.reason = 'sold';
