-- Enforce exactly one settlement per bet at the database level.
--
-- db/schema.sql already declares `bet_id TEXT NOT NULL UNIQUE`, but
-- `CREATE TABLE IF NOT EXISTS` silently skips the whole definition when the
-- table already exists. Any database created before that column gained UNIQUE
-- therefore has settlements.bet_id with no uniqueness at all, and the only
-- guard was an in-process Python set -- which cannot hold across two workers
-- or across a restart. This migration closes that hole.
--
-- Idempotent: re-running is a no-op.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'settlements'::regclass
          AND conname = 'uniq_settlement_bet_id'
    ) THEN
        RAISE NOTICE 'uniq_settlement_bet_id already present on settlements';
    ELSE
        -- Do NOT guess how to collapse duplicates. Duplicate rows mean money
        -- was paid twice, which is a reconciliation decision for a human, not
        -- something a migration should paper over.
        IF EXISTS (
            SELECT 1 FROM settlements GROUP BY bet_id HAVING COUNT(*) > 1
        ) THEN
            RAISE EXCEPTION
                'settlements contains duplicate bet_id rows; reconcile payouts before adding uniq_settlement_bet_id';
        END IF;

        ALTER TABLE settlements
            ADD CONSTRAINT uniq_settlement_bet_id UNIQUE (bet_id);
        RAISE NOTICE 'added uniq_settlement_bet_id to settlements';
    END IF;
END $$;

-- Exactly-once payout tracking. Recording the settlement and moving the money
-- are not one atomic step, so the row needs a "money actually moved" flag for
-- the settlement-retry path to find work that is still owed.
ALTER TABLE settlements
    ADD COLUMN IF NOT EXISTS credited BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE settlements
    ADD COLUMN IF NOT EXISTS credited_at TIMESTAMPTZ;
ALTER TABLE settlements
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Exactly-once payout lookups go through this index on every settle().
CREATE INDEX IF NOT EXISTS settlements_uncredited_idx
    ON settlements (bet_id) WHERE credited IS NOT TRUE;
