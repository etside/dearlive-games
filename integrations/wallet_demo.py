"""Demo wallet adapter. NON-PRODUCTION ONLY.

In-memory test wallet for demo/staging Teen Patti play. No database, no
ledger persistence, no network: every method returns immediately from local
state. Real balances (per player, seeded on first touch), real idempotency
(replay by key returns the cached result, never a second money movement),
real insufficient-funds rejection.

This is deliberately NOT a mock that always says yes: it enforces balances
and idempotency exactly like a real ledger would, so the game code paths
exercised against it (debit-on-bet, credit-on-settlement, duplicate-request
protection) are the same paths production uses.

Hard guard: constructing this adapter with APP_ENV=production raises. The
factory in integrations/__init__.py additionally refuses to select it in
production, so both layers must be misconfigured simultaneously for demo
money to exist in production. In production the flag must be absent/false.
"""
import os
import threading
import time

from common.wallet import (Balance, InsufficientBalance, TxnRef, WalletAdapter,
                           WalletError)

STARTING_BALANCE = int(os.environ.get("INITIAL_GAME_BALANCE", "10000") or 10000)


class DemoWalletAdapter(WalletAdapter):
    """In-memory test wallet. Raises if constructed in production."""

    def __init__(self, starting_balance: int = None):
        if os.environ.get("APP_ENV", "sandbox").strip().lower() == "production":
            raise RuntimeError("DemoWalletAdapter cannot run in production")
        self.starting_balance = (STARTING_BALANCE if starting_balance is None
                                 else int(starting_balance))
        self.balances = {}
        self.txns = {}      # idempotency_key -> TxnRef
        self.records = {}   # txn_id -> dict (for rollback lookup)
        self._seq = 0
        self._lock = threading.RLock()

    def _ensure(self, player_id: str) -> int:
        return self.balances.setdefault(player_id, self.starting_balance)

    def _next_id(self) -> str:
        self._seq += 1
        return f"demo-txn-{self._seq}-{int(time.time() * 1000)}"

    def get_balance(self, player_id: str) -> Balance:
        with self._lock:
            return Balance(player_id, self._ensure(player_id))

    def _once(self, player_id: str, amount: int, ref: str,
              idempotency_key: str, kind: str) -> TxnRef:
        if amount <= 0:
            raise WalletError("amount must be positive")
        if not idempotency_key:
            raise WalletError("idempotency_key is required")
        with self._lock:
            if idempotency_key in self.txns:
                return self.txns[idempotency_key]
            if kind == "debit":
                if self._ensure(player_id) < amount:
                    raise InsufficientBalance(player_id)
                self.balances[player_id] -= amount
            else:
                self.balances[player_id] = self._ensure(player_id) + amount
            txn_id = self._next_id()
            ref_out = TxnRef(txn_id=txn_id, idempotency_key=idempotency_key)
            self.txns[idempotency_key] = ref_out
            self.records[txn_id] = {"txn_id": txn_id, "player_id": player_id,
                                    "amount": amount, "ref": ref,
                                    "idempotency_key": idempotency_key,
                                    "kind": kind,
                                    "created_at": int(time.time() * 1000)}
            return ref_out

    def debit(self, player_id: str, amount: int, ref: str,
              idempotency_key: str) -> TxnRef:
        """Stake funds. Duplicate key returns the cached result, no second debit."""
        return self._once(player_id, amount, ref, idempotency_key, "debit")

    def credit(self, player_id: str, amount: int, ref: str,
               idempotency_key: str) -> TxnRef:
        """Pay out winnings. Duplicate key returns the cached result, no double credit."""
        return self._once(player_id, amount, ref, idempotency_key, "credit")

    def void_debit(self, player_id: str, ref: str,
                   idempotency_key: str) -> TxnRef:
        """Compensating path required by WalletAdapter. Demo has no separate
        void flow, so this is a no-op acknowledgement keyed like everything
        else (replay-safe, moves no money)."""
        with self._lock:
            if idempotency_key in self.txns:
                return self.txns[idempotency_key]
            ref_out = TxnRef(txn_id=self._next_id(), idempotency_key=idempotency_key)
            self.txns[idempotency_key] = ref_out
            return ref_out

    def rollback(self, player_id: str, txn_id: str) -> dict:
        """Reverse a previous debit/credit by txn_id. The reversal is itself
        recorded under a derived key, so replaying a rollback is also safe."""
        with self._lock:
            rec = self.records.get(txn_id)
            if rec is None:
                raise WalletError(f"unknown txn {txn_id!r}")
            if rec["player_id"] != player_id:
                raise WalletError("txn does not belong to player")
            if rec.get("reversed"):
                return {"success": True, "new_balance": self._ensure(player_id),
                        "txn_id": txn_id, "already_reversed": True}
            if rec["kind"] == "debit":
                self.balances[player_id] = self._ensure(player_id) + rec["amount"]
            else:
                if self._ensure(player_id) < rec["amount"]:
                    raise InsufficientBalance(player_id)
                self.balances[player_id] -= rec["amount"]
            rec["reversed"] = True
            return {"success": True, "new_balance": self.balances[player_id],
                    "txn_id": txn_id}

    def health_check(self) -> dict:
        return {"ok": True, "mode": "demo", "players": len(self.balances),
                "transactions": len(self.records)}
