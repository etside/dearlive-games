"""Settlement store: exactly one settlement per bet, enforced by the store.

Replaces the in-process `settled_bet_ids` set, which only ever guarded one
process. A plain set cannot make settlement exactly-once across two workers or
across a restart: both would see an empty set and both would pay.

The invariant now lives in the store, and the store is the thing that is
replaced per deployment:
  - PostgresSettlementStore enforces it with a real UNIQUE(bet_id) constraint
    (db/migrations/004_settlement_bet_unique.up.sql) plus INSERT .. ON CONFLICT.
  - MemorySettlementStore enforces the identical semantics in-process, so the
    test suite exercises the real contract rather than a special case.

Semantics of record():
  returns (row, created)
    created=True   this caller is the first to record this bet_id and must
                   perform the payout.
    created=False  a row already existed; the caller must NOT pay again unless
                   row["credited"] is still False (see below).

Why `credited` is tracked separately:
recording the settlement and moving the money cannot be one atomic step
without a distributed transaction. So the row records the *intent* to pay and
a separate flag records that the money moved. A worker that crashes between
the two leaves credited=False, and the settlement-retry path (Fix #1) picks it
up and pays exactly once. The wallet credit is itself idempotent on
`settle:{bet_id}`, so even a concurrent double-entry into the credit path
replays instead of paying twice.
"""
import threading
from typing import Dict, List, Optional, Tuple


class SettlementStore:
    def record(self, settlement: dict) -> Tuple[dict, bool]:
        """Atomically record a settlement for settlement["bet_id"].

        Returns (stored_row, created). Never raises on a duplicate: a duplicate
        is the expected outcome of a concurrent or replayed settle.
        """
        raise NotImplementedError

    def mark_credited(self, bet_id: str) -> None:
        """Record that the payout for bet_id has actually been paid."""
        raise NotImplementedError

    def get(self, bet_id: str) -> Optional[dict]:
        raise NotImplementedError

    def unpaid(self) -> List[dict]:
        """Recorded but not yet credited. These still owe the player money."""
        raise NotImplementedError


class MemorySettlementStore(SettlementStore):
    """In-process store with the same contract as the Postgres one.

    `storage` may be a dict shared with other store instances. Two
    MemorySettlementStore objects over one dict model two workers against one
    database, which is how the concurrency guarantee is tested without a
    live Postgres.
    """

    def __init__(self, storage: Optional[Dict[str, dict]] = None):
        self._rows: Dict[str, dict] = {} if storage is None else storage
        self._lock = threading.Lock()

    def record(self, settlement: dict) -> Tuple[dict, bool]:
        bet_id = settlement["bet_id"]
        with self._lock:
            existing = self._rows.get(bet_id)
            if existing is not None:
                # UNIQUE(bet_id) hit: the first writer's row stands.
                return dict(existing), False
            row = dict(settlement)
            row["credited"] = False
            self._rows[bet_id] = row
            return dict(row), True

    def mark_credited(self, bet_id: str) -> None:
        with self._lock:
            row = self._rows.get(bet_id)
            if row is not None:
                row["credited"] = True

    def get(self, bet_id: str) -> Optional[dict]:
        with self._lock:
            row = self._rows.get(bet_id)
            return dict(row) if row is not None else None

    def unpaid(self) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._rows.values() if not r.get("credited")]

    def settled_bet_ids(self) -> set:
        with self._lock:
            return set(self._rows.keys())
