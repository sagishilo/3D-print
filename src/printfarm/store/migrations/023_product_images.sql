-- Catalog product images (owned by the personalization agent): the preview a 3MF carries, taken at import, or a
-- picture the operator uploaded in Catalog › Edit (which a later 3MF never replaces). Kept in the database so the
-- backups hold them and a deleted product takes its image with it.

CREATE TABLE catalog.product_images (
    sku         TEXT        PRIMARY KEY REFERENCES catalog.products (sku) ON DELETE CASCADE,
    sha256      TEXT        NOT NULL,
    mime        TEXT        NOT NULL CHECK (mime IN ('image/png', 'image/jpeg', 'image/webp')),
    data        BYTEA       NOT NULL,
    source      TEXT        NOT NULL CHECK (source IN ('3mf', 'upload')),
    updated_by  TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
