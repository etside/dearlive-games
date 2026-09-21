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
from typing import Optional


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


class MemoryWallet(WalletAdapter):
    """Dev/test double. NOT for production (no durability, no concurrency)."""

    def __init__(self):
        self.balances = {}
        self.txns = {}
        self._seq = 0

    def fund(self, player_id: str, amount: int):
        b = self.balances.get(player_id, 0) + amount
        self.balances[player_id] = b

    def get_balance(self, player_id: str) -> Balance:
        return Balance(player_id, self.balances.get(player_id, 0))

    def _once(self, idempotency_key: str, make):
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
            self._seq += 1
            return TxnRef(f"txn-{self._seq}", idempotency_key)
        return self._once(idempotency_key, make)

    def credit(self, player_id: str, amount: int, ref: str, idempotency_key: str) -> TxnRef:
        if amount < 0:
            raise WalletError("amount must be non-negative")
        def make():
            self.balances[player_id] = self.balances.get(player_id, 0) + amount
            self._seq += 1
            return TxnRef(f"txn-{self._seq}", idempotency_key)
        return self._once(idempotency_key, make)

    def void_debit(self, player_id: str, ref: str, idempotency_key: str):
        # Compensate by re-crediting the original debit amount is the
        # caller's job (it knows the stake); here we only track the key.
        def make():
            self._seq += 1
            return TxnRef(f"txn-{self._seq}", idempotency_key)
        return self._once(idempotency_key, make)
