"""Short-lived provider session tokens.

The operator creates a session over the signed provider API and receives a
session_id plus a session_token (gst_...). The token is what the player client
uses as its bearer credential and what the WebSocket authenticates with, so the
B2B shared secret never reaches the APK. Tokens are opaque, short-lived and
single-purpose: they resolve to a session and can be revoked on delete.
"""
import json
import secrets
import threading
import time
from typing import Optional

TOKEN_PREFIX = "gst_"
DEFAULT_TTL_SECONDS = 1800
REDIS_PREFIX = "pvdr:session_token:"


class SessionTokenError(Exception):
    def __init__(self, message: str, code: str = "INVALID_SESSION_TOKEN"):
        super().__init__(message)
        self.code = code


class SessionTokenStore:
    def mint(self, session_id: str, claims: dict, ttl_s: int) -> dict:
        raise NotImplementedError

    def get(self, token: str) -> Optional[dict]:
        raise NotImplementedError

    def revoke(self, token: str) -> bool:
        raise NotImplementedError

    def revoke_session(self, session_id: str) -> int:
        raise NotImplementedError


class MemorySessionTokenStore(SessionTokenStore):
    def __init__(self):
        self._tokens: dict = {}
        self._by_session: dict = {}
        self._lock = threading.Lock()

    def mint(self, session_id, claims, ttl_s):
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = int(time.time() * 1000)
        record = dict(claims)
        record.update({"token": token, "session_id": session_id,
                       "created_at_ms": now,
                       "expires_at_ms": now + int(ttl_s) * 1000})
        with self._lock:
            self._tokens[token] = record
            self._by_session.setdefault(session_id, set()).add(token)
        return record

    def get(self, token):
        with self._lock:
            record = self._tokens.get(token)
            if record is None:
                return None
            if record["expires_at_ms"] <= int(time.time() * 1000):
                del self._tokens[token]
                return None
            return record

    def revoke(self, token):
        with self._lock:
            record = self._tokens.pop(token, None)
            if record is None:
                return False
            bucket = self._by_session.get(record["session_id"])
            if bucket:
                bucket.discard(token)
            return True

    def revoke_session(self, session_id):
        with self._lock:
            tokens = list(self._by_session.pop(session_id, set()))
            for token in tokens:
                self._tokens.pop(token, None)
            return len(tokens)


class RedisSessionTokenStore(SessionTokenStore):
    INDEX = "pvdr:session_tokens:{session_id}"

    def __init__(self, redis):
        self.r = redis

    def mint(self, session_id, claims, ttl_s):
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = int(time.time() * 1000)
        record = dict(claims)
        record.update({"token": token, "session_id": session_id,
                       "created_at_ms": now,
                       "expires_at_ms": now + int(ttl_s) * 1000})
        self.r.command("SET", REDIS_PREFIX + token, json.dumps(record),
                       "EX", str(int(ttl_s)))
        self.r.command("SADD", self.INDEX.format(session_id=session_id), token)
        self.r.command("EXPIRE", self.INDEX.format(session_id=session_id),
                       str(int(ttl_s)))
        return record

    def get(self, token):
        raw = self.r.command("GET", REDIS_PREFIX + token)
        if raw is None:
            return None
        return json.loads(raw)

    def revoke(self, token):
        record = self.get(token)
        if record is None:
            return False
        self.r.command("DEL", REDIS_PREFIX + token)
        self.r.command("SREM", self.INDEX.format(session_id=record["session_id"]),
                       token)
        return True

    def revoke_session(self, session_id):
        index = self.INDEX.format(session_id=session_id)
        tokens = self.r.command("SMEMBERS", index) or []
        for token in tokens:
            self.r.command("DEL", REDIS_PREFIX + token)
        self.r.command("DEL", index)
        return len(tokens)


def resolve(store: SessionTokenStore, token: str) -> Optional[dict]:
    if not token or not str(token).startswith(TOKEN_PREFIX):
        return None
    return store.get(token)
