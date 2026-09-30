#!/usr/bin/env python3
"""Re-run a failing admin query and print the exact column Postgres objects.

`UndefinedColumn` names the column but the API only surfaces the exception
type, so the operator sees "admin dashboard query failed: UndefinedColumn" and
nothing else. This runs the real query and prints
diag.diag_message / .column_name / .table_name from the server's own error.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.diag_dashboard import db_url  # noqa: E402

# Every column the dashboard and rules reads, so this stays a complete check
# rather than a snapshot of whatever was failing when it was written.
QUERIES = [
    ("dashboard.rounds_live",
     "SELECT count(*) FROM rounds WHERE status IN ('BETTING_OPEN','DEALING')"),
    ("dashboard.rounds_last_hour",
     "SELECT count(*) FROM rounds WHERE created_at_ms >= 0"),
    ("dashboard.bets_today",
     "SELECT COALESCE(sum(amount),0) FROM bets WHERE decision_time_ms >= 0"),
    ("dashboard.settlements_payout",
     "SELECT COALESCE(sum(payout),0) FROM settlements"),
    ("dashboard.settlements_owed",
     "SELECT count(*) FROM settlements WHERE credited IS NOT TRUE"),
    ("rules.versions",
     "SELECT version, confirmed, created_at FROM game_configuration "
     "WHERE game_id = %s ORDER BY created_at DESC LIMIT 10", ("teen-patti-pro",)),
    ("rules.latest",
     "SELECT version, confirmed, tbc, payload, created_at FROM game_configuration "
     "WHERE game_id = %s ORDER BY created_at DESC LIMIT 1", ("teen-patti-pro",)),
    ("dashboard.games",
     "SELECT count(*) FILTER (WHERE status = 'live'), count(*), "
     "count(*) FILTER (WHERE NOT (lower(status) NOT IN "
     "('disabled','offline','retired'))) FROM game"),
    ("games.list_admin",
     "SELECT game_id, name, status FROM game ORDER BY game_id"),
    ("packages.list",
     "SELECT package_id, name, coins, price_minor, currency, bonus_percent, "
     "bonus_coins, is_active, sort_order, tags, created_at, updated_at "
     "FROM token_package ORDER BY sort_order, package_id"),
    ("withdrawals.pending",
     "SELECT count(*) FROM withdrawal_request WHERE status = 'PENDING'"),
]


def main() -> int:
    try:
        import psycopg
    except ImportError:
        import psycopg2 as psycopg  # type: ignore
    conn = psycopg.connect(db_url())
    conn.autocommit = False
    bad = 0
    try:
        with conn.cursor() as cur:
            for q in QUERIES:
                name, sql = q[0], q[1]
                args = q[2] if len(q) > 2 else None
                try:
                    cur.execute(sql, args)
                    rows = cur.fetchall()
                    print("ok   %-28s -> %s" % (
                        name, str(rows[0])[:70] if rows else "(no rows)"))
                except Exception as exc:
                    bad += 1
                    print("FAIL %-28s" % name)
                    print("       %s" % str(exc).strip().splitlines()[0])
                    diag = getattr(exc, "diag", None)
                    if diag is not None:
                        for attr in ("table_name", "column_name", "message"):
                            val = getattr(diag, attr, None)
                            if val:
                                print("       %-12s %s" % (attr, val))
        conn.rollback()
    finally:
        conn.close()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
