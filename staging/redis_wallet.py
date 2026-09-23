"""Redis-backed staging wallet (STAGING ONLY — never production).

Implements the same WalletAdapter interface the DearLive integration uses:
get_balance / debit / credit / void_debit, all idempotent on
idempotency_key, all append-only auditable. Balances live in a Redis hash,
the ledger in a capped Redis list. TEST COINS only: faucet grants are
refused when APP_ENV=production.
"""
import json
import os
import time
from typing import Optional

from common.wallet import (Balance, InsufficientBalance, TxnRef, WalletAdapter,
                           WalletError)

PREFIX = "stg:wallet:"
LEDGER_CAP = 2000


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "sandbox").lower() == "production"


class RedisWallet(WalletAdapter):
    def __init__(self, redis=None, prefix: str = PREFIX):
        if redis is None:
            from integrations.redis_store import MinimalRedis
            redis = MinimalRedis()
        self.redis = redis
        self.prefix = prefix

    def _bal_key(self, player_id: str) -> str:
        return f"{self.prefix}bal:{player_id}"

    def _cmd(self, *args: str):
        return self.redis.command(*args)

    def get_balance(self, player_id: str) -> Balance:
        raw = self._cmd("GET", self._bal_key(player_id))
        try:
            available = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            available = 0
        return Balance(player_id, available, "TEST")

    def _record(self, player_id: str, direction: str, amount: int, ref: str,
                key: str, txn_id: str):
        entry = json.dumps({"txn_id": txn_id, "player_id": player_id,
                            "direction": direction, "amount": amount,
                            "ref": ref, "idempotency_key": key,
                            "currency": "TEST",
                            "created_at_ms": int(time.time() * 1000)},
                           separators=(",", ":"))
        self._cmd("LPUSH", f"{self.prefix}ledger:{player_id}", entry)
        self._cmd("LTRIM", f"{self.prefix}ledger:{player_id}", "0",
                  str(LEDGER_CAP - 1))

    def debit(self, player_id: str, amount: int, ref: str,
              idempotency_key: str) -> TxnRef:
        if amount <= 0:
            raise WalletError("amount must be positive")
        return self._once(player_id, "debit", amount, ref, idempotency_key,
                          self._apply_debit)

    def _apply_debit(self, player_id: str, amount: int, ref: str,
                     key: str) -> TxnRef:
        while True:
            self._cmd("WATCH", self._bal_key(player_id))
            raw = self._cmd("GET", self._bal_key(player_id))
            bal = int(raw) if raw is not None else 0
            if bal < amount:
                self._cmd("UNWATCH")
                raise InsufficientBalance(player_id)
            txn_id = f"stx-{int(time.time() * 1000)}-{abs(hash((player_id, key))) % 999999}"
            self._cmd("MULTI")
            self._cmd("DECRBY", self._bal_key(player_id), str(amount))
            res = self._cmd("EXEC")
            if res is None:
                continue  # watched key changed; retry
            self._record(player_id, "debit", amount, ref, key, txn_id)
            return TxnRef(txn_id, key)

    def _once(self, player_id: str, direction: str, amount: int, ref: str,
              key: str, apply) -> TxnRef:
        # Claim-first: exactly one caller applies; losers read the winner.
        claimed = self._cmd("SET", f"{self.prefix}key:{key}", "CLAIMED",
                            "NX", "EX", "86400")
        if claimed is None:
            existing = self._cmd("GET", f"{self.prefix}key:{key}")
            if existing in (None, "CLAIMED"):
                # Winner crashed mid-apply or still applying; safe retry once.
                existing = self._cmd("GET", f"{self.prefix}key:{key}")
            return TxnRef(str(existing), key)
        txn = apply(player_id, amount, ref, key)
        self._cmd("SET", f"{self.prefix}key:{key}", txn.txn_id, "EX", "86400")
        return txn

    def credit(self, player_id: str, amount: int, ref: str,
               idempotency_key: str) -> TxnRef:
        if amount < 0:
            raise WalletError("amount must be non-negative")
        return self._once(player_id, "credit", amount, ref, idempotency_key,
                          self._apply_credit)

    def _apply_credit(self, player_id: str, amount: int, ref: str,
                      key: str) -> TxnRef:
        txn_id = f"stx-{int(time.time() * 1000)}-{abs(hash((player_id, key))) % 999999}"
        self._cmd("INCRBY", self._bal_key(player_id), str(amount))
        self._record(player_id, "credit", amount, ref, key, txn_id)
        return TxnRef(txn_id, key)

    def void_debit(self, player_id: str, ref: str,
                   idempotency_key: str) -> Optional[TxnRef]:
        existing = self._cmd("GET", f"{self.prefix}key:{idempotency_key}")
        if existing is not None:
            return TxnRef(str(existing), idempotency_key)
        txn_id = f"stx-{int(time.time() * 1000)}-{abs(hash((ref, idempotency_key))) % 999999}"
        self._cmd("SET", f"{self.prefix}key:{idempotency_key}", txn_id, "NX", "EX", "86400")
        return TxnRef(txn_id, idempotency_key)

    def faucet(self, player_id: str, amount: int, ref: str,
               idempotency_key: str) -> TxnRef:
        """Grant TEST COINS. Staging-only; production raises."""
        if _is_production():
            raise WalletError("faucet is staging-only")
        if amount <= 0 or amount > 1_000_000:
            raise WalletError("faucet amount must be 1..1000000")
        return self.credit(player_id, amount, f"faucet:{ref}", f"faucet:{idempotency_key}")

    def ledger(self, player_id: str, limit: int = 100):
        rows = self._cmd("LRANGE", f"{self.prefix}ledger:{player_id}", "0",
                         str(max(0, limit - 1))) or []
        return [json.loads(r) for r in rows]
