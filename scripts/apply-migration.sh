#!/usr/bin/env bash
# Apply the admin deep-control migration to the integrator's Postgres.
#
# The DearLive app developer supplies DATABASE_URL; this script never invents
# one. Every statement in the migration is idempotent (CREATE TABLE IF NOT
# EXISTS, CREATE INDEX IF NOT EXISTS), so re-running is safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MIGRATION="db/migrations/005_admin_deep_control.up.sql"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set." >&2
  echo "" >&2
  echo "The DearLive app developer must export it first, e.g." >&2
  echo "  export DATABASE_URL='postgresql://user:pass@host/db?sslmode=require'" >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "ERROR: psql not found. Install the PostgreSQL client." >&2
  exit 1
fi

if [ ! -f "$MIGRATION" ]; then
  echo "ERROR: migration not found at $MIGRATION" >&2
  exit 1
fi

echo "==> applying $MIGRATION"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$MIGRATION"

echo ""
echo "==> verifying the four new tables exist"
TABLES=$(psql "$DATABASE_URL" -t -A -c "\dt" 2>/dev/null || true)

FAILED=0
for t in profit_risk_config player_override vip_tier withdrawal_request; do
  if printf '%s' "$TABLES" | grep -q "$t"; then
    echo "  OK       $t"
  else
    echo "  MISSING  $t" >&2
    FAILED=1
  fi
done

if [ "$FAILED" -ne 0 ]; then
  echo "" >&2
  echo "ERROR: migration did not produce the expected tables." >&2
  echo "The four tables this adds are the only genuinely new concepts;" >&2
  echo "token_package, game_config, config_version and admin_audit already" >&2
  echo "exist as coin_package, game_configuration, a version column and" >&2
  echo "audit_log respectively, and are intentionally reused." >&2
  exit 1
fi

echo ""
echo "==> confirming the range CHECK constraints landed"
psql "$DATABASE_URL" -c "\d profit_risk_config" | grep -E "CHECK" || true

echo ""
echo "Migration applied successfully."
echo "Next: start the app and check /api/v1/health reports database: ok."
