-- Revert 006_scheduled_config.
--
-- WARNING: cancelling pending changes is not the same as reverting them. If a
-- scheduled profit/risk change has already been APPLIED, dropping this table
-- loses the record of it while its effect stays in profit_risk_config. Capture
-- what is in flight first:
--
--   SELECT change_id, target_type, target_id, effective_at, status
--   FROM scheduled_config_change ORDER BY effective_at;

DROP INDEX IF EXISTS scheduled_config_target_idx;
DROP INDEX IF EXISTS scheduled_config_due_idx;
DROP TABLE IF EXISTS scheduled_config_change;
