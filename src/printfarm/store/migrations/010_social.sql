-- Phase 10: Social Listening (ADR-0007: posts arrive only when a group member captures them; every reply is
-- drafted here, approved by a person and posted by that person). Owned by the social agent.
-- The phase-3 placeholders social.captures / social.leads were never written to; they are replaced.
CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS social.leads;
DROP TABLE IF EXISTS social.captures;

-- 384 = sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (contracts/social.py EMBED_DIM)
CREATE TABLE social.captures (
    capture_id    TEXT PRIMARY KEY,
    group_name    TEXT NOT NULL,
    post_url      TEXT NOT NULL,
    post_text     TEXT NOT NULL,
    text_hash     TEXT NOT NULL,                       -- normalised text: the same post captured twice is one capture
    captured_by   TEXT NOT NULL,
    captured_at   TIMESTAMPTZ NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN
                    ('new', 'scoring', 'ignored', 'scored', 'lead', 'duplicate', 'waiting', 'failed')),
    status_reason TEXT NOT NULL DEFAULT '',
    embedding     vector(384),
    prefilter     REAL,                                 -- similarity to labelled buyer posts (0..1), the cheap gate
    intent        TEXT,                                 -- buy | sell | recommend | other (from the model)
    intent_score  REAL CHECK (intent_score BETWEEN 0 AND 1),
    sku_guess     TEXT,
    summary       TEXT NOT NULL DEFAULT '',
    label         TEXT CHECK (label IN ('buyer', 'not_buyer')),   -- a person's verdict: the labelled set
    labelled_by   TEXT,
    scored_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX captures_text_hash ON social.captures (text_hash);
CREATE INDEX captures_recent ON social.captures (received_at DESC);
CREATE INDEX captures_status ON social.captures (status) WHERE status IN ('new', 'scoring', 'waiting');

-- Labelled posts the prefilter compares against: the seed examples below, plus every capture a person labels.
CREATE TABLE social.examples (
    example_id  TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    label       TEXT NOT NULL CHECK (label IN ('buyer', 'not_buyer')),
    source      TEXT NOT NULL DEFAULT 'seed',           -- seed | capture
    embedding   vector(384),                            -- filled by the agent (seed rows start empty)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The catalog as the social agent sees it, for matching a post to a SKU (refreshed from catalog events).
CREATE TABLE social.sku_vectors (
    sku        TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    price_agorot INT NOT NULL,
    active     BOOLEAN NOT NULL,
    embedding  vector(384) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE social.leads (
    lead_id      TEXT PRIMARY KEY,
    capture_id   TEXT NOT NULL UNIQUE REFERENCES social.captures (capture_id),
    intent_score REAL NOT NULL,
    sku          TEXT,
    summary      TEXT NOT NULL,
    draft        TEXT NOT NULL DEFAULT '',             -- the model's Hebrew reply; never posted by the system
    draft_notes  TEXT NOT NULL DEFAULT '',
    final_text   TEXT,                                  -- what the person approved (edited or not)
    status       TEXT NOT NULL DEFAULT 'drafting' CHECK (status IN
                   ('drafting', 'pending', 'approved', 'posted', 'dismissed', 'failed')),
    status_reason TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    posted_by    TEXT,
    posted_at    TIMESTAMPTZ
);
CREATE INDEX leads_open ON social.leads (status, created_at DESC);

-- Every model call, for the daily budget and the cost shown on the Social screen.
CREATE TABLE social.llm_calls (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    purpose       TEXT NOT NULL CHECK (purpose IN ('classify', 'draft')),
    model         TEXT NOT NULL,
    capture_id    TEXT,
    input_tokens  INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    cache_read_tokens  INT NOT NULL DEFAULT 0,
    cache_write_tokens INT NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(10, 6) NOT NULL DEFAULT 0,
    outcome       TEXT NOT NULL DEFAULT 'ok'            -- ok | refused | error: ...
);
CREATE INDEX llm_calls_at ON social.llm_calls (at DESC);

-- Seed examples (Hebrew first: the farm's customers). The agent embeds them on start.
INSERT INTO social.examples (example_id, text, label) VALUES
  ('seed-b1', 'מישהו מכיר מקום שמדפיס שלט עם שם לדלת? צריך לחדר של הבת שלי', 'buyer'),
  ('seed-b2', 'מחפשת מתנה מותאמת אישית לגננת, משהו עם השם שלה', 'buyer'),
  ('seed-b3', 'איפה אפשר להזמין מעמד לטלפון או לטאבלט בהדפסה תלת מימד?', 'buyer'),
  ('seed-b4', 'ממליצים על מישהו שעושה הדפסות תלת מימד בהזמנה אישית באזור?', 'buyer'),
  ('seed-b5', 'צריכה שלטים קטנים עם שמות לשולחנות באירוע, כ-30 יחידות', 'buyer'),
  ('seed-b6', 'looking for someone to 3D print a custom name sign for a door', 'buyer'),
  ('seed-n1', 'מוכר אופניים משומשים במצב מעולה, 500 שקל', 'not_buyer'),
  ('seed-n2', 'הכלב שלנו ברח באזור, מי שראה שיתקשר בבקשה', 'not_buyer'),
  ('seed-n3', 'מישהו ממליץ על אינסטלטור טוב ולא יקר?', 'not_buyer'),
  ('seed-n4', 'אני מדפיס בתלת מימד, מוכר מדפסת ישנה שלי', 'not_buyer'),
  ('seed-n5', 'שבת שלום לכולם! תמונות מהטיול שלנו בגליל', 'not_buyer');

-- Capture extension tokens (one per browser/device). Only the SHA-256 is kept; the token is shown once.
CREATE TABLE gateway.capture_tokens (
    token_id     TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    uses         INT NOT NULL DEFAULT 0,
    revoked_at   TIMESTAMPTZ
);
