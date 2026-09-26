-- Scheduled configuration changes: apply a rule, profit or settings change at
-- a chosen date and time instead of immediately.
--
-- Why a table and not a cron entry: an operator schedules from the admin UI
-- and then needs to see, cancel and audit what is pending. A crontab gives you
-- none of that, and it is invisible to the audit trail.
--
-- "Changes apply to the NEXT round only" still holds, and for a structural
-- reason rather than a convention: a round snapshots the config version it
-- started with, so a change activated mid-round cannot alter a round already
-- in flight. See games/teen_patti_pro/service.py (config_snapshot).
--
-- Status is constrained so the activation sweep cannot invent a state, and the
-- partial index serves the one query that matters: what is due now.
--
-- Idempotent: re-running is a no-op.

CREATE TABLE IF NOT EXISTS scheduled_config_change (
    change_id    TEXT PRIMARY KEY,
    target_type  TEXT        NOT NULL,
    target_id    TEXT        NOT NULL DEFAULT '',
    payload      JSONB       NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'PENDING',
    applied_at   TIMESTAMPTZ,
    result       JSONB,
    created_by   TEXT        NOT NULL DEFAULT '',
    reason       TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT scheduled_target_known CHECK (
        target_type IN ('profit_risk', 'game_config', 'settings', 'package')
    ),
    CONSTRAINT scheduled_status_known CHECK (
        status IN ('PENDING', 'APPLIED', 'CANCELLED', 'FAILED')
    )
);

-- The activation sweep: everything pending whose time has arrived, oldest
-- first so a backlog drains in the order it was scheduled.
CREATE INDEX IF NOT EXISTS scheduled_config_due_idx
    ON scheduled_config_change (effective_at)
    WHERE status = 'PENDING';

-- Per-target history, so the admin UI can show what is queued for one game.
CREATE INDEX IF NOT EXISTS scheduled_config_target_idx
    ON scheduled_config_change (target_type, target_id, effective_at DESC);

-- Reject a change scheduled in the past at insert time in the API layer; the
-- database only enforces that a timestamp exists.
