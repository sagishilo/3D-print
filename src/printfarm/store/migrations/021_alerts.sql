-- Wave 2 / W2-D: notifications delivered to people (Telegram, e-mail).
--   notify.outbox.delivery: per channel, what happened to this notification: {"telegram": "sent" | "skipped" |
--     "held" | "failed"} (a channel that already sent it never sends it again, even when another one failed).
--   notify.outbox.next_at: when to try again (after a failure, or when quiet hours end).
ALTER TABLE notify.outbox ADD COLUMN delivery JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE notify.outbox ADD COLUMN next_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX outbox_due ON notify.outbox (status, next_at) WHERE status IN ('pending', 'held');
