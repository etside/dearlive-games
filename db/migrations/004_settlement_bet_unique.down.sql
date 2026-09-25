-- Revert 004_settlement_bet_unique.
--
-- WARNING: dropping this constraint removes the database-level guarantee that
-- a bet is settled at most once. Exactly-once settlement then depends solely
-- on the settlement store's insert path. Only run this if you are prepared to
-- re-audit payouts.

DROP INDEX IF EXISTS settlements_uncredited_idx;

ALTER TABLE settlements DROP CONSTRAINT IF EXISTS uniq_settlement_bet_id;
