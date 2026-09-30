#!/usr/bin/env python3
"""Dry-run a migration's statements against the real database in a transaction.

Parses the .sql file and executes every statement inside a transaction that is
always rolled back, so the host's schema is checked without being changed. A
migration that references a table or column that does not exist fails here
before it is ever applied for real.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.diag_dashboard import db_url  # noqa: E402


def split_statements(sql: str):
    """Split on semicolons outside of quotes, comments and dollar-quotes."""
    out, buf, i, n = [], [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1])
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: verify_migration.py <file.sql> [...]")
    try:
        import psycopg
    except ImportError:
        import psycopg2 as psycopg  # type: ignore

    url = db_url()
    failures = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        stmts = split_statements(path.read_text(encoding="utf-8"))
        print("=== %s (%d statements) ===" % (path.name, len(stmts)))
        conn = psycopg.connect(url)
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                for k, stmt in enumerate(stmts, 1):
                    head = " ".join(stmt.split())[:70]
                    try:
                        cur.execute(stmt)
                        print("  ok   %2d  %s" % (k, head))
                    except Exception as exc:
                        failures += 1
                        print("  FAIL %2d  %s" % (k, head))
                        print("       %s" % str(exc).strip().splitlines()[0])
            conn.rollback()
            print("  -> rolled back, schema unchanged")
        except Exception as exc:
            failures += 1
            print("  FATAL %s" % exc)
        finally:
            conn.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
