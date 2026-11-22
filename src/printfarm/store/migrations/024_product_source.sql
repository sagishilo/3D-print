-- Where a catalog product comes from (contracts/sources.py): its model page (MakerWorld, Printables, Thingiverse),
-- designer and licence (from the page, the 3MF or typed in), and, for a licence that does not allow selling prints,
-- who confirmed the commercial rights before it went on sale.

ALTER TABLE catalog.products
    ADD COLUMN source_url          TEXT,                   -- canonical model page; NULL: none recorded
    ADD COLUMN source_site         TEXT CHECK (source_site IN ('makerworld', 'printables', 'thingiverse')),
    ADD COLUMN source_id           TEXT,                   -- the site's model id
    ADD COLUMN designer            TEXT NOT NULL DEFAULT '',
    ADD COLUMN licence             TEXT NOT NULL DEFAULT '',
    ADD COLUMN rights_confirmed_by TEXT,
    ADD COLUMN rights_confirmed_at TIMESTAMPTZ;
