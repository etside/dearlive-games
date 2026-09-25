"""Postgres settlement store: UNIQUE(bet_id) makes double payout impossible.

The uniqueness guarantee is the database's, not this code's. `record()` relies
on INSERT .. ON CONFLICT (bet_id) DO NOTHING, so two workers racing on the
same bet cannot both create a row: the loser gets no RETURNING row and then
reads back the winner's row. A plain IntegrityError is also handled, so a
deployment whose constraint predates ON CONFLICT support still behaves.

Connection handling is deliberately minimal: the caller supplies a
DB-API-like cursor factory with .execute()/.fetchone()/.fetchall() and
commit/rollback on the connection. That keeps this file stdlib-only and
compatible with psycopg, psycopg2 and the project's MinimalRedis-style
adapters without adding a driver dependency here.
"""
from typing import List, Optional, Tuple

from common.settlement import SettlementStore

_COLUMNS = ("settlement_id", "bet_id", "player_id", "payout", "config_version")


class PostgresSettlementStore(SettlementStore):
    """cursor_factory() -> a fresh DB-API cursor (and its connection is
    reachable as cursor.connection)."""

    def __init__(self, cursor_factory):
        self._cursor_factory = cursor_factory

    # -- internals --

    def _select(self, cur, bet_id: str):
        cur.execute(
            "SELECT settlement_id, bet_id, player_id, payout, config_version, credited "
            "FROM settlements WHERE bet_id = %s",
            (bet_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return {"settlement_id": row[0], "bet_id": row[1], "player_id": row[2],
                "payout": row[3], "config_version": row[4], "credited": bool(row[5])}

    def _commit(self, cur):
        conn = getattr(cur, "connection", None)
        if conn is not None and hasattr(conn, "commit"):
            conn.commit()

    def _rollback(self, cur):
        conn = getattr(cur, "connection", None)
        if conn is not None and hasattr(conn, "rollback"):
            conn.rollback()

    # -- contract --

    def record(self, settlement: dict) -> Tuple[dict, bool]:
        bet_id = settlement["bet_id"]
        cur = self._cursor_factory()
        try:
            cur.execute(
                "INSERT INTO settlements "
                "(settlement_id, bet_id, player_id, payout, config_version, credited) "
                "VALUES (%s, %s, %s, %s, %s, FALSE) "
                "ON CONFLICT (bet_id) DO NOTHING "
                "RETURNING settlement_id, bet_id, player_id, payout, config_version, credited",
                (settlement.get("settlement_id", ""), bet_id, settlement["player_id"],
                 settlement["payout"], settlement.get("config_version", "")))
            row = cur.fetchone()
            if row is not None:
                self._commit(cur)
                return ({"settlement_id": row[0], "bet_id": row[1], "player_id": row[2],
                         "payout": row[3], "config_version": row[4],
                         "credited": bool(row[5])}, True)
            # Conflict: someone else owns this payout. Read theirs back so the
            # caller sees one consistent row rather than a fabricated "created".
            existing = self._select(cur, bet_id)
            self._commit(cur)
            if existing is None:
                raise RuntimeError(
                    f"settlement for bet {bet_id!r} conflicted but no row was found")
            return existing, False
        except Exception as exc:
            self._rollback(cur)
            # A unique-violation that slipped past ON CONFLICT (older server,
            # different constraint) is still a duplicate, not a failure.
            if _is_unique_violation(exc):
                cur2 = self._cursor_factory()
                try:
                    existing = self._select(cur2, bet_id)
                    if existing is not None:
                        return existing, False
                finally:
                    pass
            raise
        finally:
            close = getattr(cur, "close", None)
            if close:
                close()

    def mark_credited(self, bet_id: str) -> None:
        cur = self._cursor_factory()
        try:
            cur.execute(
                "UPDATE settlements SET credited = TRUE, credited_at = NOW() "
                "WHERE bet_id = %s", (bet_id,))
            self._commit(cur)
        except Exception:
            self._rollback(cur)
            raise
        finally:
            close = getattr(cur, "close", None)
            if close:
                close()

    def get(self, bet_id: str) -> Optional[dict]:
        cur = self._cursor_factory()
        try:
            return self._select(cur, bet_id)
        finally:
            close = getattr(cur, "close", None)
            if close:
                close()

    def unpaid(self) -> List[dict]:
        cur = self._cursor_factory()
        try:
            cur.execute(
                "SELECT settlement_id, bet_id, player_id, payout, config_version, credited "
                "FROM settlements WHERE credited IS NOT TRUE")
            return [{"settlement_id": r[0], "bet_id": r[1], "player_id": r[2],
                     "payout": r[3], "config_version": r[4], "credited": bool(r[5])}
                    for r in cur.fetchall() or []]
        finally:
            close = getattr(cur, "close", None)
            if close:
                close()


def _is_unique_violation(exc: Exception) -> bool:
    """True for a Postgres unique_violation (SQLSTATE 23505)."""
    diag = getattr(exc, "diag", None)
    if diag is not None and getattr(diag, "sqlstate", None) == "23505":
        return True
    code = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)
    if code == "23505":
        return True
    return "23505" in str(exc) or "duplicate key value" in str(exc).lower()
