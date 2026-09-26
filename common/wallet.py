"""Wallet adapter interface.

The game engine NEVER touches balances directly. All money movement goes
through this interface, which the DearLive backend implements.

CLIENT API REQUIRED: exact DearLive wallet endpoints are closed-source
(zero /api/v1/ strings recoverable from stripped APK). The implementer
must map these five methods to prod endpoints and document the mapping
in docs/wallet-integration.md.

Ordering guarantee (enforced by the engine, not the adapter):
  auth -> game -> round -> action -> amount -> min/max -> balance ->
  window -> idempotency -> atomic debit -> create bet -> publish
Settlement: one bet = one settlement (UNIQUE settlement.bet_id),
idempotency key on every financial write, immutable transactions.
"""
from dataclasses import dataclass
import time
from typing import List, Optional


@dataclass(frozen=True)
class Balance:
    player_id: str
    available: int  # minor units (e.g. coins); integers only, no floats
    currency: str = "COIN"


@dataclass(frozen=True)
class TxnRef:
    txn_id: str
    idempotency_key: str


class WalletError(Exception):
    pass


class InsufficientBalance(WalletError):
    pass


class WalletAdapter:
    """DearLive implements this. Memory impl below is for tests/dev only."""

    def get_balance(self, player_id: str) -> Balance:
        raise NotImplementedError  # CLIENT API REQUIRED

    def debit(self, player_id: str, amount: int, ref: str,
              idempotency_key: str) -> TxnRef:
        """Atomically reserve stake. Must be idempotent on (ref, key).
        Raises InsufficientBalance if funds unavailable."""
        raise NotImplementedError  # CLIENT API REQUIRED

    def credit(self, player_id: str, amount: int, ref: str,
               idempotency_key: str) -> TxnRef:
        """Pay out winnings exactly once per ref. Idempotent."""
        raise NotImplementedError  # CLIENT API REQUIRED

    def void_debit(self, player_id: str, ref: str,
                   idempotency_key: str) -> Optional[TxnRef]:
        """Compensating credit for cancelled rounds. Immutable ledger:
        never delete, only compensate."""
        raise NotImplementedError  # CLIENT API REQUIRED

    def transactions(self, player_id: str, limit: int = 50) -> List[dict]:
        """Ledger entries for one player, newest first.

        Optional: a client wallet that exposes its own statement endpoint
        should implement this. When it does not, the API answers 501 rather
        than inventing a statement.
        """
        raise NotImplementedError  # CLIENT API OPTIONAL


# SRS section 9 wallet types. Kept as constants so a typo is an AttributeError
# at import rather than a row in an immutable ledger nobody can correct.
TXN_COIN_PURCHASE = "COIN_PURCHASE"
TXN_BET_DEBIT = "BET_DEBIT"
TXN_WIN_CREDIT = "WIN_CREDIT"
TXN_BONUS = "BONUS"
TXN_ADMIN_CREDIT = "ADMIN_CREDIT"
TXN_WITHDRAWAL_HOLD = "WITHDRAWAL_HOLD"
TXN_WITHDRAWAL_COMPLETED = "WITHDRAWAL_COMPLETED"
TXN_REFUND = "REFUND"
TXN_TYPES = (TXN_COIN_PURCHASE, TXN_BET_DEBIT, TXN_WIN_CREDIT, TXN_BONUS,
             TXN_ADMIN_CREDIT, TXN_WITHDRAWAL_HOLD, TXN_WITHDRAWAL_COMPLETED,
             TXN_REFUND)


class MemoryWallet(WalletAdapter):
    """Dev/test double. NOT for production (no durability, no concurrency).

    Keeps an append-only ledger as well as balances, because SRS section 9
    requires the balance to be verifiable from the sum of a player's
    transactions. Balances alone would make a discrepancy undiagnosable: there
    would be no way to tell a rounding bug from a lost payout.
    """

    def __init__(self):
        import threading
        self.ledger: List[dict] = []
        self.balances = {}
        self.txns = {}
        self._seq = 0
        # Reentrant, not Lock: _once() holds the lock while it calls make(),
        # and make() now records a ledger row through _append(), which takes the
        # lock again. A plain Lock deadlocks on the second acquisition.
        self._lock = threading.RLock()

    def fund(self, player_id: str, amount: int):
        """Seed a test balance. Recorded in the ledger as ADMIN_CREDIT so the
        balance still reconciles against the transaction sum."""
        b = self.balances.get(player_id, 0) + amount
        self.balances[player_id] = b
        self._append(player_id, TXN_ADMIN_CREDIT, amount, "fund",
                     f"fund:{player_id}:{b}")

    def _append(self, player_id: str, kind: str, amount: int, ref: str,
                idempotency_key: str) -> dict:
        """Append one ledger row. amount is signed: debits negative."""
        if kind not in TXN_TYPES:
            raise WalletError(f"unknown transaction type {kind!r}")
        with self._lock:
            self._seq += 1
            row = {"txn_id": f"txn-{self._seq}", "player_id": player_id,
                   "type": kind, "amount": int(amount), "ref": ref,
                   "idempotency_key": idempotency_key,
                   "created_at": int(time.time() * 1000)}
            self.ledger.append(row)
            return row

    def transactions(self, player_id: str, limit: int = 50) -> List[dict]:
        """Newest first, bounded. limit is clamped: an unbounded statement on
        a long-lived player is a memory problem and a data leak."""
        limit = max(1, min(int(limit or 50), 500))
        with self._lock:
            rows = [r for r in self.ledger if r["player_id"] == player_id]
        return list(reversed(rows))[:limit]

    def verify_balance(self, player_id: str) -> dict:
        """Recompute the balance from the ledger and compare.

        This is the check that makes the ledger worth keeping: if the stored
        balance and the sum of transactions ever disagree, something moved
        money without writing a row.
        """
        with self._lock:
            stored = self.balances.get(player_id, 0)
            computed = sum(r["amount"] for r in self.ledger
                           if r["player_id"] == player_id)
            count = sum(1 for r in self.ledger if r["player_id"] == player_id)
        return {"player_id": player_id, "balance": stored,
                "ledger_sum": computed, "reconciled": stored == computed,
                "transaction_count": count,
                "difference": stored - computed}

    def get_balance(self, player_id: str) -> Balance:
        return Balance(player_id, self.balances.get(player_id, 0))

    def _once(self, idempotency_key: str, make):
        with self._lock:
            if idempotency_key in self.txns:
                return self.txns[idempotency_key]
            ref = make()
            self.txns[idempotency_key] = ref
            return ref

    def debit(self, player_id: str, amount: int, ref: str, idempotency_key: str) -> TxnRef:
        if amount <= 0:
            raise WalletError("amount must be positive")
        def make():
            if self.balances.get(player_id, 0) < amount:
                raise InsufficientBalance(player_id)
            self.balances[player_id] -= amount
            row = self._append(player_id, TXN_BET_DEBIT, -abs(amount), ref,
                               idempotency_key)
            return TxnRef(row["txn_id"], idempotency_key)
        return self._once(idempotency_key, make)

    def credit(self, player_id: str, amount: int, ref: str, idempotency_key: str) -> TxnRef:
        if amount < 0:
            raise WalletError("amount must be non-negative")
        def make():
            self.balances[player_id] = self.balances.get(player_id, 0) + amount
            row = self._append(player_id, TXN_WIN_CREDIT, abs(amount), ref,
                               idempotency_key)
            return TxnRef(row["txn_id"], idempotency_key)
        return self._once(idempotency_key, make)

    def void_debit(self, player_id: str, ref: str, idempotency_key: str):
        # Compensate by re-crediting the original debit amount is the
        # caller's job (it knows the stake); here we only track the key.
        def make():
            self._seq += 1
            return TxnRef(f"txn-{self._seq}", idempotency_key)
        return self._once(idempotency_key, make)
