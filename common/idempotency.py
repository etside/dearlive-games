"""Idempotency-key store interface + in-memory implementation.

Every financial write (bet, settlement) MUST carry a client-supplied
idempotency key. Same key + same payload -> replay stored result.
Same key + different payload -> E_CONFLICT (never execute twice).

claim() returns an EXPLICIT state, never a bare None. The previous contract
returned None both for "freshly claimed, go execute" and for "another worker
holds this key and has not finished", so callers could not tell a first claim
from an in-flight duplicate and would re-enter the money path. provider.ledger
already used the correct fresh/stored split; this brings the game path in line.

States:
  CLAIMED    caller now owns the key: execute, then complete() or release()
  REPLAY     a prior execution completed; .result holds its stored value
  IN_FLIGHT  another worker holds the key with no result yet -> 409, retry
  NOT_SEEN   key absent (peek() only; claim() never returns this)

Use `outcome.state is ClaimResult.X` -- deliberately no truthiness, so the
None-ambiguity cannot come back.

Production backend: swap MemoryIdempotencyStore for the Redis impl in
integrations/redis_store.py (same contract).
"""
import time
from enum import Enum
from typing import Any, Dict, NamedTuple, Optional


class IdempotencyConflict(Exception):
    def __init__(self, key: str):
        super().__init__(f"Idempotency key {key!r} already used with different payload")
        self.key = key


class ClaimResult(str, Enum):
    CLAIMED = "CLAIMED"
    REPLAY = "REPLAY"
    IN_FLIGHT = "IN_FLIGHT"
    NOT_SEEN = "NOT_SEEN"


class ClaimOutcome(NamedTuple):
    """`state` is the explicit ClaimResult; `result` is set only for REPLAY."""

    state: ClaimResult
    result: Optional[Dict[str, Any]] = None


class IdempotencyStore:
    def peek(self, key: str, payload_hash: str) -> ClaimOutcome:
        """Inspect without claiming. Returns NOT_SEEN / IN_FLIGHT / REPLAY.
        Raises IdempotencyConflict if the key exists with a different payload."""
        raise NotImplementedError

    def claim(self, key: str, payload_hash: str, ttl_s: int = 86400) -> ClaimOutcome:
        """Atomically claim key.

        Returns ClaimOutcome(CLAIMED) on a fresh claim, ClaimOutcome(REPLAY,
        stored_result) if a prior execution completed, or
        ClaimOutcome(IN_FLIGHT) if another worker holds it. Never returns
        NOT_SEEN. Raises IdempotencyConflict on a payload mismatch.
        The CLAIMED caller MUST follow up with complete() or release().
        """
        raise NotImplementedError

    def complete(self, key: str, result: Dict[str, Any]) -> None:
        """Attach the completed result to a claimed key."""
        raise NotImplementedError

    def release(self, key: str, payload_hash: Optional[str] = None) -> bool:
        """Drop an in-flight claim so the key is usable again.

        Called on every failure path where NO money moved and NO bet was
        created, so a client retry is not locked out for the whole TTL.
        Never removes a completed result: returns True only if an in-flight
        entry was actually released.
        """
        raise NotImplementedError


class MemoryIdempotencyStore(IdempotencyStore):
    def __init__(self):
        import threading
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()  # claim check-and-set must be atomic

    def _purge(self):
        now = time.time()
        for k in [k for k, v in self._entries.items() if v["expires"] <= now]:
            del self._entries[k]

    def peek(self, key: str, payload_hash: str) -> ClaimOutcome:
        with self._lock:
            self._purge()
            ent = self._entries.get(key)
            if ent is None:
                return ClaimOutcome(ClaimResult.NOT_SEEN)
            if ent["payload_hash"] != payload_hash:
                raise IdempotencyConflict(key)
            if ent.get("result") is None:
                return ClaimOutcome(ClaimResult.IN_FLIGHT)
            return ClaimOutcome(ClaimResult.REPLAY, ent["result"])

    def claim(self, key: str, payload_hash: str, ttl_s: int = 86400) -> ClaimOutcome:
        with self._lock:
            self._purge()
            ent = self._entries.get(key)
            if ent is not None:
                if ent["payload_hash"] != payload_hash:
                    raise IdempotencyConflict(key)
                if ent.get("result") is None:
                    return ClaimOutcome(ClaimResult.IN_FLIGHT)  # another worker
                return ClaimOutcome(ClaimResult.REPLAY, ent["result"])
            self._entries[key] = {"payload_hash": payload_hash,
                                  "result": None,
                                  "expires": time.time() + ttl_s}
            return ClaimOutcome(ClaimResult.CLAIMED)

    def complete(self, key: str, result: Dict[str, Any]) -> None:
        with self._lock:
            ent = self._entries.get(key)
            if ent is None:
                raise KeyError(f"Idempotency key {key!r} was never claimed")
            ent["result"] = result

    def release(self, key: str, payload_hash: Optional[str] = None) -> bool:
        """Release an in-flight claim. Refuses to drop a completed result.

        If payload_hash is supplied it must match, so a caller can never
        release a key that a different payload now owns.
        """
        with self._lock:
            self._purge()
            ent = self._entries.get(key)
            if ent is None or ent.get("result") is not None:
                return False  # absent, or already completed: never drop a result
            if payload_hash is not None and ent["payload_hash"] != payload_hash:
                return False
            del self._entries[key]
            return True
