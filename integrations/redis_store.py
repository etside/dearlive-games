"""Redis-backed launch-token, session, and idempotency stores.

Contract (DearLive host mints into the SAME Redis the game server reads):
  key:   {TOKEN_KEY_PREFIX}{token}   (default prefix "dearlive:launch:")
  value: JSON {player_id, room_id, game_id, expires_at_ms, nonce}
  TTL:   TOKEN_TTL_MS (default 120s). Redeem = GET + DEL (single-use atomic
         via Lua; fallback GET+DEL when Lua unavailable).

Idempotency (prod): SET key JSON NX PX ttl  (claim), then SET with result
(complete). Same key + different payload hash -> IdempotencyConflict.

Stdlib only: minimal RESP client over socket (+TLS via ssl when REDIS_TLS).
If redis is unreachable at construction, stores raise ConnectionError with
a clear message — callers (factory) fall back to memory stores in sandbox.
"""
import json
import os
import secrets
import socket
import ssl
import time
from typing import Any, Dict, Optional

from common.idempotency import (IdempotencyConflict, IdempotencyStore, ClaimResult,
                                ClaimOutcome)
from common.session import (LaunchToken, Session, SessionStore, TokenError,
                            TokenStore)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class RedisConnectionError(ConnectionError):
    pass


def _from_url(url: str) -> dict:
    """Parse redis:// or rediss:// into discrete connection settings.

    Hosted providers (Vercel Marketplace, Upstash) usually inject a single
    REDIS_URL; explicit REDIS_* variables still win when both are present.
    """
    try:
        import urllib.parse as _up
        parsed = _up.urlparse(url)
    except Exception:
        return {}
    if not parsed.hostname:
        return {}
    return {
        "host": parsed.hostname,
        "port": int(parsed.port or 6379),
        "username": _up.unquote(parsed.username or ""),
        "password": _up.unquote(parsed.password or ""),
        "use_tls": parsed.scheme == "rediss",
        "db": int((parsed.path or "/0").lstrip("/") or 0),
    }


class MinimalRedis:
    """Tiny RESP2 client: GET/SET(NX,PX)/DEL/EXPIRE/EVAL. Enough for the contract."""

    def __init__(self, host: str = "", port: Optional[int] = None,
                 db: Optional[int] = None, username: str = "", password: str = "",
                 use_tls: bool = False, timeout: float = 5.0):
        from_url = _from_url(_env("REDIS_URL", ""))
        self.host = host or _env("REDIS_HOST", "") or from_url.get("host", "127.0.0.1")
        self.port = int(port or _env("REDIS_PORT", "") or from_url.get("port") or 6379)
        self.db = int(db if db is not None else _env("REDIS_DB", "") or from_url.get("db") or 0)
        self.username = username or _env("REDIS_USERNAME", "") or from_url.get("username", "")
        self.password = password or _env("REDIS_PASSWORD", "") or from_url.get("password", "")
        self.use_tls = (use_tls
                        or _env("REDIS_TLS", "false").lower() in ("1", "true", "yes")
                        or from_url.get("use_tls", False))
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._connect()

    # -- wire --
    def _connect(self):
        try:
            s: socket.socket = socket.create_connection((self.host, self.port),
                                                        timeout=self.timeout)
            if self.use_tls:
                s = ssl.wrap_socket(s)
            self._sock = s
        except OSError as exc:
            raise RedisConnectionError(f"redis {self.host}:{self.port}: {exc}")
        if self.password:
            args = ["AUTH", self.password] if not self.username else \
                ["AUTH", self.username, self.password]
            self.command(*args)
        if self.db:
            self.command("SELECT", str(self.db))

    def _send(self, *args: str):
        assert self._sock is not None
        parts = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = a.encode() if isinstance(a, str) else a
            parts += [f"${len(b)}\r\n".encode(), b, b"\r\n"]
        self._sock.sendall(b"".join(parts))

    def _readline(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise RedisConnectionError("redis connection closed")
            self._buf += chunk
        line, _, rest = self._buf.partition(b"\r\n")
        self._buf = rest
        return line

    def _read(self) -> Any:
        line = self._readline()
        typ, payload = line[:1], line[1:]
        if typ == b"+":
            return payload.decode()
        if typ == b"-":
            raise RedisConnectionError(f"redis error: {payload.decode()}")
        if typ == b":":
            return int(payload)
        if typ == b"$":
            n = int(payload)
            if n == -1:
                return None
            need = n + 2
            data = self._buf[:need]
            self._buf = self._buf[need:]
            while len(data) < need:
                chunk = self._sock.recv(need - len(data))
                if not chunk:
                    raise RedisConnectionError("redis connection closed")
                if len(data) + len(chunk) > need:
                    data += chunk[:need - len(data)]
                    self._buf = chunk[need - len(data):] + self._buf
                else:
                    data += chunk
            return data[:n].decode()
        if typ == b"*":
            n = int(payload)
            if n == -1:
                return None
            return [self._read() for _ in range(n)]
        raise RedisConnectionError(f"unknown RESP type: {typ!r}")

    def command(self, *args: str) -> Any:
        try:
            self._send(*args)
            return self._read()
        except RedisConnectionError:
            raise
        except OSError as exc:
            raise RedisConnectionError(str(exc))

    def close(self):
        try:
            if self._sock:
                self._sock.close()
        finally:
            self._sock = None


class RedisTokenStore(TokenStore):
    """Single-use launch tokens in shared Redis. Game-scoped, TTL-enforced."""

    REDEEM_LUA = ("if redis.call('GET',KEYS[1])==false then return nil end "
                  "local v=redis.call('GET',KEYS[1]); redis.call('DEL',KEYS[1]); return v")

    def __init__(self, redis: Optional[MinimalRedis] = None, prefix: str = "",
                 ttl_ms: int = 0):
        self.redis = redis or MinimalRedis()
        self.prefix = prefix or _env("TOKEN_KEY_PREFIX", "dearlive:launch:")
        self.ttl_ms = ttl_ms or int(_env("TOKEN_TTL_MS", "120000") or 120000)

    def _key(self, token: str) -> str:
        return f"{self.prefix}{token}"

    def mint(self, player_id: str, room_id: str, game_id: str) -> LaunchToken:
        tok = f"dl_{game_id}_{secrets.token_urlsafe(24)}"
        lt = LaunchToken(tok, player_id, room_id, game_id,
                         int(time.time() * 1000) + self.ttl_ms,
                         secrets.token_hex(8))
        payload = json.dumps({"player_id": player_id, "room_id": room_id,
                              "game_id": game_id,
                              "expires_at_ms": lt.expires_at_ms, "nonce": lt.nonce})
        self.redis.command("SET", self._key(tok), payload, "PX", str(self.ttl_ms))
        return lt

    def redeem(self, token: str) -> LaunchToken:
        if not token.startswith("dl_"):
            raise TokenError("malformed launch token (expect dl_<game>_...)")
        key = self._key(token)
        try:
            raw = self.redis.command("EVAL", self.REDEEM_LUA, "1", key)
        except RedisConnectionError:
            raw = self.redis.command("GET", key)
            if raw is not None:
                self.redis.command("DEL", key)
        if raw is None:
            raise TokenError("unknown or replayed launch token")
        try:
            claims = json.loads(raw)
        except ValueError:
            raise TokenError("corrupt launch token claims")
        if int(claims.get("expires_at_ms", 0)) < int(time.time() * 1000):
            raise TokenError("expired launch token")
        return LaunchToken(token, str(claims["player_id"]), str(claims["room_id"]),
                           str(claims["game_id"]), int(claims["expires_at_ms"]),
                           str(claims.get("nonce", "")))


class RedisSessionStore(SessionStore):
    """Sessions in Redis (prefix dearlive:session:). Touch updates last_seen."""

    def __init__(self, redis: Optional[MinimalRedis] = None, prefix: str = "dearlive:session:",
                 ttl_s: int = 86400):
        self.redis = redis or MinimalRedis()
        self.prefix = prefix
        self.ttl_s = ttl_s

    def create(self, player_id: str, room_id: str, game_id: str) -> Session:
        now = int(time.time() * 1000)
        sid = f"dl-sess-{secrets.token_hex(8)}"
        s = Session(sid, player_id, room_id, game_id, now, now)
        self.redis.command("SET", self.prefix + sid,
                           json.dumps({"player_id": player_id, "room_id": room_id,
                                       "game_id": game_id, "created_at_ms": now,
                                       "last_seen_ms": now}),
                           "EX", str(self.ttl_s))
        return s

    def get(self, session_id: str) -> Optional[Session]:
        raw = self.redis.command("GET", self.prefix + session_id)
        if raw is None:
            return None
        d = json.loads(raw)
        return Session(session_id, d["player_id"], d["room_id"], d["game_id"],
                       d["created_at_ms"], d["last_seen_ms"])

    def touch(self, session_id: str, snapshot=None) -> Optional[Session]:
        s = self.get(session_id)
        if s is None:
            return None
        s.last_seen_ms = int(time.time() * 1000)
        self.redis.command("SET", self.prefix + session_id,
                           json.dumps({"player_id": s.player_id, "room_id": s.room_id,
                                       "game_id": s.game_id,
                                       "created_at_ms": s.created_at_ms,
                                       "last_seen_ms": s.last_seen_ms}),
                           "KEEPTTL" if False else "EX", str(self.ttl_s))
        return s

    def end(self, session_id: str) -> None:
        self.redis.command("DEL", self.prefix + session_id)


class RedisIdempotencyStore(IdempotencyStore):
    """SET NX EX claim + result attach. Prefix dearlive:idem:.

    Mirrors MemoryIdempotencyStore's explicit ClaimResult contract. The
    previous version returned a bare None for both a fresh claim and an
    in-flight duplicate, which is the same ambiguity the memory store had.
    """

    def __init__(self, redis: Optional[MinimalRedis] = None, prefix: str = "dearlive:idem:"):
        self.redis = redis or MinimalRedis()
        self.prefix = prefix

    def _read(self, key: str):
        raw = self.redis.command("GET", self.prefix + key)
        return json.loads(raw) if raw is not None else None

    def _check_payload(self, key: str, ent: dict, payload_hash: str) -> None:
        if ent.get("payload_hash") != payload_hash:
            raise IdempotencyConflict(key)

    def peek(self, key: str, payload_hash: str) -> ClaimOutcome:
        ent = self._read(key)
        if ent is None:
            return ClaimOutcome(ClaimResult.NOT_SEEN)
        self._check_payload(key, ent, payload_hash)
        if ent.get("result") is None:
            return ClaimOutcome(ClaimResult.IN_FLIGHT)
        return ClaimOutcome(ClaimResult.REPLAY, ent.get("result"))

    def claim(self, key: str, payload_hash: str, ttl_s: int = 86400) -> ClaimOutcome:
        ent = self._read(key)
        if ent is not None:
            self._check_payload(key, ent, payload_hash)
            if ent.get("result") is None:
                return ClaimOutcome(ClaimResult.IN_FLIGHT)
            return ClaimOutcome(ClaimResult.REPLAY, ent.get("result"))
        new = json.dumps({"payload_hash": payload_hash, "result": None})
        ok = self.redis.command("SET", self.prefix + key, new, "NX", "EX", str(ttl_s))
        if ok is None:  # raced: another worker inserted between GET and SET
            return self.claim(key, payload_hash, ttl_s)
        return ClaimOutcome(ClaimResult.CLAIMED)

    def complete(self, key: str, result: Dict[str, Any]) -> None:
        raw = self.redis.command("GET", self.prefix + key)
        if raw is None:
            raise KeyError(f"Idempotency key {key!r} was never claimed")
        ent = json.loads(raw)
        ent["result"] = result
        self.redis.command("SET", self.prefix + key, json.dumps(ent), "EX", "86400")

    def release(self, key: str, payload_hash: Optional[str] = None) -> bool:
        """Drop an in-flight claim. Never drops a completed result.

        GET-then-DEL is not atomic, so this is a best-effort release: a worker
        that completes between the read and the delete would lose its result.
        Callers only release on failure paths where nothing completed, and the
        read guard makes that safe in practice. A Lua CAS would be stronger if
        the Redis client grows EVAL support.
        """
        ent = self._read(key)
        if ent is None or ent.get("result") is not None:
            return False
        if payload_hash is not None and ent.get("payload_hash") != payload_hash:
            return False
        self.redis.command("DEL", self.prefix + key)
        return True
