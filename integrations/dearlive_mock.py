"""Mock DearLive adapters — DearLive-shaped doubles for local play + contract tests.

These implement the SAME interfaces the production DearLive adapters will
implement (common.wallet.WalletAdapter, common.session.TokenStore/SessionStore).
The contract suite (tests/test_adapter_contract.py) runs against these now and
against the real DearLive adapters later WITHOUT touching the game engine
(handover rule §13: Engine -> Common Interface -> DearLive Adapter -> DearLive API).

DearLive-shaped behavior (mirrors prod expectations from APK audit):
- Coin balances as integers; txn ids `DL-TXN-<n>`; launch tokens `dl_<game>_<rand>`
  carrying claims {player_id, room_id, game_id, exp_ms, nonce}; single-use redeem.
- PLAYER_STATUS hook: banned/suspended players rejected at session open.
- Failure injection (tests only): timeouts, flaky debit, forced insufficient —
  the engine must survive all of them without losing money.
"""
import secrets
import time
from typing import Dict, List, Optional

from common.session import LaunchToken, Session, SessionStore, TokenError, TokenStore
from common.wallet import Balance, InsufficientBalance, TxnRef, WalletAdapter, WalletError


class MockDearLiveWallet(WalletAdapter):
    """Ledger-style mock: append-only txn list, idempotent keys, injectable faults."""

    def __init__(self, latency_ms: int = 0):
        self.balances: Dict[str, int] = {}
        self.ledger: List[dict] = []
        self._keys: Dict[str, TxnRef] = {}
        self._seq = 0
        self.latency_ms = latency_ms
        self.fail_next: List[str] = []  # e.g. ["timeout", "insufficient"]

    def fund(self, player_id: str, amount: int):
        self.balances[player_id] = self.balances.get(player_id, 0) + amount

    def _fault(self, op: str):
        if self.fail_next:
            f = self.fail_next.pop(0)
            if f == "timeout":
                raise WalletError(f"{op}: simulated DearLive timeout")
            if f == "insufficient":
                raise InsufficientBalance("simulated")
            if f == "transport":
                raise WalletError(f"{op}: simulated transport error")

    def _wait(self):
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)

    def get_balance(self, player_id: str) -> Balance:
        self._wait()
        return Balance(player_id, self.balances.get(player_id, 0), "COIN")

    def _record(self, kind: str, player_id: str, amount: int, ref: str,
                key: str) -> TxnRef:
        if key in self._keys:  # idempotent replay
            return self._keys[key]
        self._seq += 1
        txn = TxnRef(f"DL-TXN-{self._seq:08d}", key)
        self.ledger.append({"txn_id": txn.txn_id, "kind": kind, "player": player_id,
                            "amount": amount, "ref": ref, "at": int(time.time() * 1000)})
        self._keys[key] = txn
        return txn

    def debit(self, player_id: str, amount: int, ref: str, idempotency_key: str) -> TxnRef:
        self._fault("debit")
        self._wait()
        if amount <= 0:
            raise WalletError("amount must be positive")
        if idempotency_key in self._keys:
            return self._keys[idempotency_key]
        if self.balances.get(player_id, 0) < amount:
            raise InsufficientBalance(player_id)
        self.balances[player_id] -= amount
        return self._record("debit", player_id, -amount, ref, idempotency_key)

    def credit(self, player_id: str, amount: int, ref: str, idempotency_key: str) -> TxnRef:
        self._fault("credit")
        self._wait()
        if amount < 0:
            raise WalletError("amount must be non-negative")
        if idempotency_key in self._keys:
            return self._keys[idempotency_key]
        self.balances[player_id] = self.balances.get(player_id, 0) + amount
        return self._record("credit", player_id, amount, ref, idempotency_key)

    def void_debit(self, player_id: str, ref: str, idempotency_key: str) -> Optional[TxnRef]:
        self._wait()
        if idempotency_key in self._keys:
            return self._keys[idempotency_key]
        # Compensating entry; caller re-credits the stake via credit().
        return self._record("void", player_id, 0, ref, idempotency_key)


class MockDearLiveTokens(TokenStore):
    """Launch tokens shaped like prod expectation: dl_<game>_<secret> + claims."""

    def __init__(self, ttl_ms: int = 120_000):
        self._store: Dict[str, LaunchToken] = {}
        self.ttl_ms = ttl_ms

    def mint(self, player_id: str, room_id: str, game_id: str) -> LaunchToken:
        tok = f"dl_{game_id}_{secrets.token_urlsafe(24)}"
        lt = LaunchToken(tok, player_id, room_id, game_id,
                         int(time.time() * 1000) + self.ttl_ms,
                         secrets.token_hex(8))
        self._store[tok] = lt
        return lt

    def redeem(self, token: str) -> LaunchToken:
        if not token.startswith("dl_"):
            raise TokenError("malformed launch token (expect dl_<game>_...)")
        lt = self._store.pop(token, None)
        if lt is None:
            raise TokenError("unknown or replayed launch token")
        if lt.expires_at_ms < int(time.time() * 1000):
            raise TokenError("expired launch token")
        return lt


class MockDearLiveSessions(SessionStore):
    """Session store with PLAYER_STATUS gate (banned players rejected)."""

    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._seq = 0
        self.banned: set = set()

    def create(self, player_id: str, room_id: str, game_id: str) -> Session:
        if player_id in self.banned:
            raise TokenError(f"player {player_id} suspended (PLAYER_STATUS)")
        self._seq += 1
        now = int(time.time() * 1000)
        s = Session(f"dl-sess-{self._seq}-{secrets.token_hex(4)}", player_id,
                    room_id, game_id, now, now)
        self._sessions[s.session_id] = s
        return s

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def touch(self, session_id: str, snapshot=None) -> Optional[Session]:
        s = self._sessions.get(session_id)
        if s is None:
            return None
        s.last_seen_ms = int(time.time() * 1000)
        if snapshot is not None:
            s.state_snapshot = snapshot
        return s

    def end(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
