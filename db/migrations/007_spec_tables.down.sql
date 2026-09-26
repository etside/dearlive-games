-- 007 down: drop the tables added by 007_spec_tables.up.sql.
--
-- Only the tables this migration created. The 001-006 tables (player, wallet,
-- wallet_transaction, profit_risk_config, player_override, coin_config,
-- superadmin_audit, settlements, ...) are left alone: they predate this
-- migration and other code still reads them.
--
-- Consequences of running this, stated so nobody runs it blind:
--   * every game_round / game_bet / game_result / game_settlement row is lost.
--     These are the money records. Export them first if they matter.
--   * config_version history is lost, so rollback past this point stops working.
--
-- Idempotent: re-running is a no-op.

DROP TABLE IF EXISTS game_settlement;
DROP TABLE IF EXISTS game_result;
DROP TABLE IF EXISTS game_bet;
DROP TABLE IF EXISTS game_round;
DROP TABLE IF EXISTS game_option;
DROP TABLE IF EXISTS game_configuration;
DROP TABLE IF EXISTS config_version;
DROP TABLE IF EXISTS admin_audit;
DROP TABLE IF EXISTS token_package;
DROP TABLE IF EXISTS game;
