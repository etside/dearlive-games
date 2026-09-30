#!/usr/bin/env python3
"""Print the live schema for every table the admin console queries.

The admin dashboard was returning UndefinedColumn. The SQL in
common/admin_store.py is written against a schema that does not exist on this
host, and the error text names a column but not the table or the query, so the
fix requires knowing what is actually there. This prints it.

Read-only: it opens a connection, reads information_schema, and closes.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def db_url() -> str:
    env_path = ROOT / ".env"
    if env_path.is_file():
        text = env_path.read_text(encoding="utf-8")
        m = re.search(r'^\s*DATABASE_URL\s*=\s*(.+)$', text, re.M)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("no DATABASE_URL in .env or the environment")
    return url


def main() -> int:
    try:
        import psycopg
    except ImportError:
        try:
            import psycopg2 as psycopg  # type: ignore
        except ImportError:
            sys.exit("neither psycopg nor psycopg2 is installed")

    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename")
            tables = [t for (t,) in cur.fetchall()]
            print("=== TABLES (%d) ===" % len(tables))
            for t in tables:
                print("  " + t)

            for tbl in ["game_round", "game_bet", "game_result",
                        "game_settlement", "player", "wallet",
                        "profit_risk_config", "token_package",
                        "coin_package", "game_configuration", "admin_audit",
                        "rounds", "bets", "settlements", "withdrawal_request"]:
                print("\n=== %s ===" % tbl)
                cur.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position", (tbl,))
                rows = cur.fetchall()
                if not rows:
                    print("  MISSING")
                    continue
                for col, typ in rows:
                    print("  %-32s %s" % (col, typ))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
