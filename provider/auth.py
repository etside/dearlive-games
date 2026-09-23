"""Operator request authentication for the B2B provider API.

Every server-to-server call carries four headers:

  X-API-Key    public key id, never the secret
  X-Timestamp  unix seconds, must sit inside the freshness window
  X-Nonce      unique per request, rejected on replay
  X-Signature  hex(HMAC-SHA256(secret, canonical))

canonical = METHOD \\n PATH \\n TIMESTAMP \\n NONCE \\n SHA256(raw_body)

PATH is the request path only; the query string is never signed, so clients
must not append it before computing the signature. The shared secret is read
from PROVIDER_API_KEYS, is never returned in a response body, never logged,
and never embedded in a launch URL. Player-facing surfaces receive only a
short-lived session token.
"""
import hashlib
import hmac
import os
import re
import threading
import time
from typing import Dict, Optional, Tuple

from common import envelope as E

DEFAULT_WINDOW_SECONDS = 300
DEFAULT_RATE_LIMIT = 600
DEFAULT_RATE_WINDOW_SECONDS = 60
MAX_NONCE_LEN = 128
KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9._~-]{8,128}$")

E_REPLAY = "REPLAYED_REQUEST"
E_SIGNATURE = "INVALID_SIGNATURE"
E_TIMESTAMP = "INVALID_TIMESTAMP"
E_NONCE = "INVALID_NONCE"


class ProviderAuthError(Exception):
    def __init__(self, message: str, code: str, status: int = 401,
                 headers: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.headers = headers or {}


def load_api_keys(raw: Optional[str] = None) -> Dict[str, str]:
    """Parse PROVIDER_API_KEYS=key_id:secret,key_id2:secret2."""
    if raw is None:
        raw = os.environ.get("PROVIDER_API_KEYS", "")
    keys: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key_id, secret = part.split(":", 1)
        key_id, secret = key_id.strip(), secret.strip()
        if key_id and secret:
            keys[key_id] = secret
    return keys


def header(headers, name: str, default: str = "") -> str:
    """Case-insensitive header read for dicts and email.message.Message alike."""
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(name)
        if value is not None:
            return value
    lowered = name.lower()
    for k, v in dict(headers or {}).items():
        if str(k).lower() == lowered:
            return str(v)
    return default


def canonical(method: str, path: str, timestamp, nonce: str, body: bytes) -> bytes:
    return "\n".join([
        str(method).upper(),
        str(path),
        str(timestamp),
        str(nonce),
        hashlib.sha256(body or b"").hexdigest(),
    ]).encode()


def sign_request(secret: str, method: str, path: str, timestamp,
                 nonce: str, body: bytes = b"") -> str:
    return hmac.new(secret.encode(), canonical(method, path, timestamp, nonce, body),
                    hashlib.sha256).hexdigest()


class NonceStore:
    def claim(self, key_id: str, nonce: str, ttl_s: int) -> bool:
        """True when the nonce is fresh, False when it was already used."""
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


class MemoryNonceStore(NonceStore):
    def __init__(self):
        self._seen: Dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, key_id: str, nonce: str, ttl_s: int) -> bool:
        now = time.time()
        composite = f"{key_id}:{nonce}"
        with self._lock:
            for k, exp in list(self._seen.items()):
                if exp <= now:
                    del self._seen[k]
            if composite in self._seen:
                return False
            self._seen[composite] = now + ttl_s
            return True

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()


class RedisNonceStore(NonceStore):
    KEY = "pvdr:nonce:{key_id}:{nonce}"

    def __init__(self, redis):
        self.r = redis

    def claim(self, key_id: str, nonce: str, ttl_s: int) -> bool:
        got = self.r.command("SET", self.KEY.format(key_id=key_id, nonce=nonce),
                             "1", "NX", "EX", str(int(ttl_s)))
        return got == "OK"

    def reset(self) -> None:
        self.r.command("DEL", self.KEY.format(key_id="*", nonce="*"))


class RateLimiter:
    """Fixed-window counter, memory or Redis backed."""

    KEY = "pvdr:rate:{bucket}:{window_start}"

    def __init__(self, limit: int = DEFAULT_RATE_LIMIT,
                 window_s: int = DEFAULT_RATE_WINDOW_SECONDS, redis=None):
        self.limit = max(1, int(limit))
        self.window_s = max(1, int(window_s))
        self.r = redis
        self._local: Dict[str, int] = {}
        self._lock = threading.Lock()

    def _window_start(self, now: float) -> int:
        return int(now // self.window_s)

    def check(self, bucket: str, now: Optional[float] = None) -> Tuple[bool, int, int]:
        """Returns (allowed, remaining, retry_after_seconds)."""
        now = time.time() if now is None else now
        start = self._window_start(now)
        retry_after = self.window_s - int(now % self.window_s)
        if self.r is not None:
            key = self.KEY.format(bucket=bucket, window_start=start)
            count = int(self.r.command("INCR", key))
            if count == 1:
                self.r.command("EXPIRE", key, str(self.window_s + 1))
            if count > self.limit:
                return False, 0, retry_after
            return True, self.limit - count, retry_after
        key = f"{bucket}:{start}"
        with self._lock:
            for k, _ in list(self._local.items()):
                if k.split(":", 1)[1] != str(start):
                    del self._local[k]
            count = self._local.get(key, 0) + 1
            self._local[key] = count
        if count > self.limit:
            return False, 0, retry_after
        return True, self.limit - count, retry_after

    def reset(self) -> None:
        with self._lock:
            self._local.clear()


def authenticate(headers, method: str, path: str, body: bytes,
                 keys: Dict[str, str], nonce_store: NonceStore,
                 limiter: Optional[RateLimiter] = None,
                 window_s: int = DEFAULT_WINDOW_SECONDS,
                 now: Optional[float] = None) -> str:
    """Verify a signed provider request and return the authenticated key id.

    Raises ProviderAuthError on any failure. Replay is only checked after the
    signature verifies, so an unsigned attacker cannot burn a legitimate nonce.
    """
    if not keys:
        raise ProviderAuthError(
            "provider credentials are not configured", E.E_AUTH, 401)
    key_id = header(headers, "X-API-Key").strip()
    signature = header(headers, "X-Signature").strip()
    nonce = header(headers, "X-Nonce").strip()
    raw_ts = header(headers, "X-Timestamp").strip()
    if not key_id or not signature or not nonce or not raw_ts:
        raise ProviderAuthError(
            "X-API-Key, X-Timestamp, X-Nonce and X-Signature are required",
            E.E_AUTH, 401)
    if not KEY_ID_RE.match(key_id):
        raise ProviderAuthError("unknown API key", E.E_AUTH, 401)
    secret = keys.get(key_id)
    if secret is None:
        raise ProviderAuthError("unknown API key", E.E_AUTH, 401)
    try:
        timestamp = int(raw_ts)
    except (TypeError, ValueError):
        raise ProviderAuthError("X-Timestamp must be unix seconds", E_TIMESTAMP, 401)
    if not NONCE_RE.match(nonce) or len(nonce) > MAX_NONCE_LEN:
        raise ProviderAuthError("X-Nonce is malformed", E_NONCE, 401)
    current = time.time() if now is None else now
    if abs(current - timestamp) > window_s:
        raise ProviderAuthError(
            "request timestamp is outside the accepted window", E_TIMESTAMP, 401)
    expected = sign_request(secret, method, path, timestamp, nonce, body or b"")
    if not hmac.compare_digest(expected, signature.lower()):
        raise ProviderAuthError("signature mismatch", E_SIGNATURE, 401)
    if limiter is not None:
        allowed, remaining, retry_after = limiter.check(key_id, now=current)
        if not allowed:
            raise ProviderAuthError(
                "rate limit exceeded", E.E_RATE_LIMIT, 429,
                {"Retry-After": str(max(1, retry_after))})
    if not nonce_store.claim(key_id, nonce, int(window_s) + 1):
        raise ProviderAuthError("nonce already used", E_REPLAY, 401)
    return key_id
