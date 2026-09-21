"""Idempotency-key store interface + in-memory implementation.

Every financial write (bet, settlement) MUST carry a client-supplied
idempotency key. Same key + same payload -> replay stored result.
Same key + different payload -> E_CONFLICT (never execute twice).

Production backend: swap MemoryIdempotencyStore for Redis impl
(SET key result NX EX <ttl>) — interface is identical.
"""
import time
from typing import Any, Dict, Optional


class IdempotencyConflict(Exception):
    def __init__(self, key: str):
        super().__init__(f"Idempotency key {key!r} already used with different payload")
        self.key = key


class IdempotencyStore:
    def claim(self, key: str, payload_hash: str, ttl_s: int = 86400) -> Optional[Dict[str, Any]]:
        """Atomically claim key. Returns stored result on replay, None if newly claimed.
        Raises IdempotencyConflict if key exists with a different payload hash."""
        raise NotImplementedError

    def complete(self, key: str, result: Dict[str, Any]) -> None:
        """Attach the completed result to a claimed key."""
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

    def claim(self, key: str, payload_hash: str, ttl_s: int = 86400):
        with self._lock:
            self._purge()
            ent = self._entries.get(key)
            if ent is not None:
                if ent["payload_hash"] != payload_hash:
                    raise IdempotencyConflict(key)
                return ent.get("result")  # None => in-flight; caller must treat as conflict/retry
            self._entries[key] = {"payload_hash": payload_hash,
                                  "result": None,
                                  "expires": time.time() + ttl_s}
            return None

    def complete(self, key: str, result: Dict[str, Any]) -> None:
        with self._lock:
            ent = self._entries.get(key)
            if ent is None:
                raise KeyError(f"Idempotency key {key!r} was never claimed")
            ent["result"] = result
