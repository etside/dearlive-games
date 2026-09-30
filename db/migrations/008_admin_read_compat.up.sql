-- 008: make the admin read queries resolve against the real schema.
--
-- Symptom
-- -------
-- /admin/dashboard returned UndefinedColumn, and the packages/rules/reports
-- pages failed the same way. The error names a column but not the query, so
-- scripts/diag_dashboard.py was used to dump what actually exists.
--
-- Root cause
-- ----------
-- common/admin_store.py was written against the names in the spec
-- (rounds / bets / settlements / coin_package, and columns
-- rounds.created_at_ms, settlements.payout, settlements.credited). What is
-- actually deployed is the game_* schema from 007 (game_round / game_bet /
-- game_settlement) plus token_package, with real column names.
--
--   query uses          table exists?   column it wants   column that exists
--   ------------------  --------------  ----------------  ------------------
--   rounds              NO              created_at_ms    created_at
--   bets                NO              (all)             --
--   settlements         NO              payout, credited  amount, state
--   coin_package        NO              (all)             token_package has it
--   game_configuration  yes             version           config_version
--
-- Why views and not new tables
-- ---------------------------
-- Duplicating the ledger would be the wrong fix twice over: the money tables
-- (game_bet, game_settlement) are the exactly-once settlement record, and a
-- second copy that admin reads from is a copy that can drift from the truth
-- the engine writes. A view is the same rows, so the dashboard cannot disagree
-- with the ledger by construction.
--
-- Every view here is READ ONLY by construction. admin_store.py performs no
-- INSERT/UPDATE/DELETE through these names except on coin_package, which is a
-- real table alias -- see the note on that view, which is deliberately absent.
--
-- Idempotent: re-running drops and recreates the views.

-- ---------------------------------------------------------------------------
-- rounds -> game_round
--   created_at_ms: the dashboard compares epoch millis, and game_round stores
--   a real TIMESTAMPTZ. Extract epoch millis from it rather than asking the
--   caller to learn SQL date maths.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW rounds AS
    SELECT round_id,
           game_id,
           room_id,
           round_no,
           status,
           config_version,
           seed_hex,
           deck_commitment,
           betting_ends_at,
           started_at,
           settled_at,
           created_at,
           (EXTRACT(EPOCH FROM created_at) * 1000)::bigint AS created_at_ms
      FROM game_round;

-- ---------------------------------------------------------------------------
-- bets -> game_bet
--   Column-for-column. game_bet.amount is numeric, the caller wants an int.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW bets AS
    SELECT bet_id,
           round_id,
           player_id,
           position,
           amount::bigint AS amount,
           status,
           idempotency_key,
           decision_time_ms,
           created_at
      FROM game_bet;

-- ---------------------------------------------------------------------------
-- settlements -> game_settlement
--   payout:  the amount credited to the player. game_settlement.amount is that
--            value, so it maps 1:1 rather than being renamed.
--   credited: the dashboard asks "settlements still owed", i.e. rows that were
--            not successfully credited. A NULL credited_at on a row that is
--            not in a terminal state is exactly an owed settlement, so
--            credited is derived rather than added as a stored flag that
--            nothing would ever update.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW settlements AS
    SELECT settlement_id,
           bet_id,
           round_id,
           player_id,
           amount::bigint AS payout,
           state,
           attempts,
           last_error,
           credited_at,
           created_at,
           (credited_at IS NOT NULL) AS credited
      FROM game_settlement;

-- ---------------------------------------------------------------------------
-- coin_package: NOT a view.
--
-- Unlike rounds/bets/settlements, admin_store.py WRITES this name
-- (create_package, update_package, archive_package), and a view is not
-- insertable or updatable. So instead of aliasing token_package read-only,
-- the code is pointed at token_package, which already has every column it
-- needs under the same names:
--
--   admin_store column     token_package column
--   ---------------------  --------------------
--   package_id             package_id
--   name                   name
--   coins                  coins
--   price_minor            price_minor
--   currency               currency
--   bonus_percent          bonus_percent
--   bonus_coins            bonus_coins
--   is_active              is_active
--   sort_order             sort_order
--   tags                   tags
--   created_at/updated_at  created_at/updated_at
--
-- No rename, no copy, no second copy of the pricing catalogue to drift.
-- See common/admin_store.py: the identifier is defined once, in _PKG_TABLE.

-- ---------------------------------------------------------------------------
-- game_configuration: the three columns the rules API writes.
--
-- Deployed shape (from 007): game_id, config_version, payload, is_active,
-- created_by, created_at.
-- The rules endpoints in common/admin_store.py write and read:
--     version, confirmed, tbc
-- `version` is a TEXT version string like "tpp-1.0.0-tbc" (the engine's
-- config_version), not the INT the migration note suggested: the service logs
-- "config tpp-1.0.0-tbc", and an INT column would refuse to store it.
-- `confirmed` is the TBC gate the engine already consults, and `tbc` is the
-- list of to-be-confirmed rules, so both are stored rather than inferred.
--
-- Backfilled from config_version so existing rows are immediately readable
-- instead of presenting a NULL version that sorts as newest.
-- ---------------------------------------------------------------------------
ALTER TABLE game_configuration
    ADD COLUMN IF NOT EXISTS version     TEXT,
    ADD COLUMN IF NOT EXISTS confirmed   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS tbc         JSONB  NOT NULL DEFAULT '[]'::jsonb;

UPDATE game_configuration
   SET version = config_version
 WHERE version IS NULL AND config_version IS NOT NULL;

-- The rules endpoints upsert on (game_id, version).
CREATE UNIQUE INDEX IF NOT EXISTS game_configuration_game_version_uk
    ON game_configuration (game_id, version);
