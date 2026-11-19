-- Wave 1 / M1: people who sign in to the dashboard, each with a role.
--   owner     everything, including users, finance settings, system settings, dead letters and capture tokens
--   operator  day-to-day work: floor, orders, fleet, filament, catalog, social review, payments and expenses
--   viewer    read-only on the operational screens
-- The gateway owns this table (like refresh_tokens and audit_log). On first start with an empty table it creates
-- the owner from ADMIN_USERNAME / ADMIN_PASSWORD_HASH in .env, so an existing install keeps its login.
CREATE TABLE gateway.users (
    id                   BIGSERIAL   PRIMARY KEY,
    username             TEXT        NOT NULL,
    display_name         TEXT        NOT NULL DEFAULT '',
    role                 TEXT        NOT NULL CHECK (role IN ('owner', 'operator', 'viewer')),
    password_hash        TEXT        NOT NULL,          -- argon2id; the password itself is never stored
    must_change_password BOOLEAN     NOT NULL DEFAULT false,   -- after an owner set a temporary password
    disabled             BOOLEAN     NOT NULL DEFAULT false,
    session_version      INTEGER     NOT NULL DEFAULT 1,       -- bumped to end every session (disable, reset, role)
    prefs                JSONB       NOT NULL DEFAULT '{}'::jsonb,   -- theme, density, mode, niche (later milestones)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by           TEXT        NOT NULL DEFAULT '',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at        TIMESTAMPTZ,
    password_changed_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX users_username_key ON gateway.users (lower(username));

-- Each session belongs to a user. Sessions from before this migration have none: they belong to the bootstrap owner.
ALTER TABLE gateway.refresh_tokens ADD COLUMN user_id BIGINT REFERENCES gateway.users (id) ON DELETE CASCADE;
CREATE INDEX refresh_tokens_user ON gateway.refresh_tokens (user_id) WHERE revoked_at IS NULL;

-- The audit log viewer filters by person, action and time.
CREATE INDEX audit_log_created ON gateway.audit_log (created_at DESC);
CREATE INDEX audit_log_actor ON gateway.audit_log (actor, created_at DESC);
