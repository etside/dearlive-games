-- Revert 005_admin_deep_control.
--
-- WARNING: profit_risk_config is the live source of house edge, payout caps and
-- loss limits. Dropping it does not revert the engine to a safe default -- the
-- engine treats "no configuration" as "use compiled defaults", which are not
-- the values a live table was holding. Capture the active row before dropping
-- if the deployment must be reversible:
--
--   SELECT * FROM profit_risk_config WHERE active;

DROP INDEX IF EXISTS withdrawal_request_player_idx;
DROP INDEX IF EXISTS withdrawal_request_pending_idx;
DROP TABLE IF EXISTS withdrawal_request;

DROP INDEX IF EXISTS vip_tier_rank_uniq;
DROP TABLE IF EXISTS vip_tier;

DROP INDEX IF EXISTS player_override_player_live_idx;
DROP TABLE IF EXISTS player_override;

DROP INDEX IF EXISTS profit_risk_config_single_active;
DROP INDEX IF EXISTS profit_risk_config_version_uniq;
DROP TABLE IF EXISTS profit_risk_config;
