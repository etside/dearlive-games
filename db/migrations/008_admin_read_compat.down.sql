-- 008 down: remove the read-compat views and the added config columns.
--
-- The views are dropped first because they depend on both the base tables and
-- (for coin_package) nothing -- but leaving them would shadow a future re-run.
-- The added columns are dropped because they were added here; the base
-- game_configuration columns are untouched, so the pre-008 shape is restored
-- exactly. Column drops discard any rules version history stored in them, so
-- take a copy before running this on a database that has saved rules.

DROP VIEW IF EXISTS rounds;
DROP VIEW IF EXISTS bets;
DROP VIEW IF EXISTS settlements;

DROP INDEX IF EXISTS game_configuration_game_version_uk;

ALTER TABLE game_configuration
    DROP COLUMN IF EXISTS tbc,
    DROP COLUMN IF EXISTS confirmed,
    DROP COLUMN IF EXISTS version;
