"""Launch-token + session interfaces (DearLive WebView pattern).

Proven flow (from APK .env + vendor Leadercc notes):
  host app mints launch token server-side into the SAME Redis the game
  server reads; game validates token (else connection error); WebView
  opens {GAMES_BASE_URL}/<game>/?token=...; session handshake follows.

CLIENT API REQUIRED: prod mint endpoint + Redis schema/key prefix/TTL
live in the closed dearlive-backend. Ship a Redis impl against the
documented contract; keep Memory impl for dev/tests.
"""
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, List


@dataclass(frozen=True)
class LaunchToken:
    token: str
    player_id: str
    room_id: str
    game_id: str
    expires_at_ms: int
    nonce: str = ""


@dataclass
class Session:
    session_id: str
    player_id: str
    room_id: str
    game_id: str
    created_at_ms: int
    last_seen_ms: int
    state_snapshot: dict = field(default_factory=dict)


class TokenError(Exception):
    pass


class TokenStore:
    TOKEN_TTL_MS = 120_000  # short-lived launch tokens (2 min)

    def mint(self, player_id: str, room_id: str, game_id: str) -> LaunchToken:
        raise NotImplementedError

    def redeem(self, token: str) -> LaunchToken:
        """Single-use: validate + consume. Raises TokenError on
        unknown/expired/replayed token (client maps to 10001-style error)."""
        raise NotImplementedError


class MemoryTokenStore(TokenStore):
    def __init__(self):
        self._tokens: Dict[str, LaunchToken] = {}

    def mint(self, player_id: str, room_id: str, game_id: str) -> LaunchToken:
        tok = secrets.token_urlsafe(32)
        lt = LaunchToken(tok, player_id, room_id, game_id,
                         int(time.time() * 1000) + self.TOKEN_TTL_MS,
                         secrets.token_hex(8))
        self._tokens[tok] = lt
        return lt

    def redeem(self, token: str) -> LaunchToken:
        lt = self._tokens.pop(token, None)
        if lt is None:
            raise TokenError("unknown or replayed launch token")
        if lt.expires_at_ms < int(time.time() * 1000):
            raise TokenError("expired launch token")
        return lt


# ============================================================
# Demo Session Support (Spribe-style demo mode)
# ============================================================

@dataclass
class DemoSession:
    session_id: str
    game_slug: str
    starting_balance: int
    current_balance: int
    currency: str
    lang: str
    created_at_ms: int
    expires_at_ms: int
    ip_hash: str
    status: str  # ACTIVE | EXPIRED | CLOSED
    rounds_played: int = 0


class DemoSessionStore:
    """In-memory demo session store with TTL and rate limiting."""
    
    # Rate limiting: max 5 sessions per IP per hour
    MAX_SESSIONS_PER_IP_PER_HOUR = 5
    MAX_ROUNDS_PER_SESSION = 100
    DEFAULT_TTL_MS = 30 * 60 * 1000  # 30 minutes
    DEFAULT_DEMO_BALANCE = 10000
    
    def __init__(self):
        self._sessions: Dict[str, DemoSession] = {}
        self._ip_sessions: Dict[str, List[tuple]] = {}  # ip_hash -> [(session_id, created_at_ms)]
        self._seq = 0
    
    def _hash_ip(self, ip: str) -> str:
        return hashlib.sha256(ip.encode()).hexdigest()[:16]
    
    def _cleanup_expired(self, now_ms: int) -> None:
        expired = [sid for sid, s in self._sessions.items() if s.expires_at_ms < now_ms]
        for sid in expired:
            s = self._sessions.pop(sid, None)
            if s:
                ip_hash = s.ip_hash
                self._ip_sessions[ip_hash] = [(sid2, t) for sid2, t in self._ip_sessions.get(ip_hash, []) if sid2 != sid]
    
    def _check_rate_limit(self, ip_hash: str, now_ms: int) -> bool:
        """Check if IP has exceeded rate limit. Returns True if allowed."""
        hour_ago = now_ms - 3600 * 1000
        recent = [(sid, t) for sid, t in self._ip_sessions.get(ip_hash, []) if t > hour_ago]
        self._ip_sessions[ip_hash] = recent
        return len(recent) < self.MAX_SESSIONS_PER_IP_PER_HOUR
    
    def create(self, game_slug: str, ip: str, currency: str = "USD", 
               lang: str = "EN", demo_balance: int = None, 
               ttl_ms: int = None) -> DemoSession:
        """Create a new demo session. Raises ValueError if rate limited."""
        now_ms = int(time.time() * 1000)
        self._cleanup_expired(now_ms)
        
        ip_hash = self._hash_ip(ip)
        if not self._check_rate_limit(ip_hash, now_ms):
            raise ValueError("Rate limit exceeded: max 5 demo sessions per IP per hour")
        
        ttl = ttl_ms or self.DEFAULT_TTL_MS
        balance = demo_balance or self.DEFAULT_DEMO_BALANCE
        
        self._seq += 1
        session_id = f"demo-{self._seq}-{secrets.token_hex(8)}"
        
        session = DemoSession(
            session_id=session_id,
            game_slug=game_slug,
            starting_balance=balance,
            current_balance=balance,
            currency=currency,
            lang=lang,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + ttl,
            ip_hash=ip_hash,
            status="ACTIVE",
            rounds_played=0
        )
        
        self._sessions[session_id] = session
        self._ip_sessions.setdefault(ip_hash, []).append((session_id, now_ms))
        return session
    
    def get(self, session_id: str) -> Optional[DemoSession]:
        return self._sessions.get(session_id)
    
    def get_active(self, session_id: str) -> Optional[DemoSession]:
        session = self._sessions.get(session_id)
        if session and session.status == "ACTIVE" and session.expires_at_ms > int(time.time() * 1000):
            return session
        return None
    
    def update_balance(self, session_id: str, amount: int) -> Optional[DemoSession]:
        session = self._sessions.get(session_id)
        if session and session.status == "ACTIVE":
            session.current_balance = max(0, session.current_balance + amount)
            return session
        return None
    
    def record_round(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session and session.status == "ACTIVE":
            session.rounds_played += 1
            if session.rounds_played >= self.MAX_ROUNDS_PER_SESSION:
                session.status = "CLOSED"
            return True
        return False
    
    def close(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session:
            session.status = "CLOSED"
            return True
        return False
    
    def record_ip(self, ip: str) -> str:
        return self._hash_ip(ip)


class TokenError(Exception):
    pass


class TokenStore:
    TOKEN_TTL_MS = 120_000  # short-lived launch tokens (2 min)

    def mint(self, player_id: str, room_id: str, game_id: str) -> LaunchToken:
        raise NotImplementedError

    def redeem(self, token: str) -> LaunchToken:
        """Single-use: validate + consume. Raises TokenError on
        unknown/expired/replayed token (client maps to 10001-style error)."""
        raise NotImplementedError


class MemoryTokenStore(TokenStore):
    def __init__(self):
        self._tokens: Dict[str, LaunchToken] = {}

    def mint(self, player_id: str, room_id: str, game_id: str) -> LaunchToken:
        tok = secrets.token_urlsafe(32)
        lt = LaunchToken(tok, player_id, room_id, game_id,
                         int(time.time() * 1000) + self.TOKEN_TTL_MS,
                         secrets.token_hex(8))
        self._tokens[tok] = lt
        return lt

    def redeem(self, token: str) -> LaunchToken:
        lt = self._tokens.pop(token, None)
        if lt is None:
            raise TokenError("unknown or replayed launch token")
        if lt.expires_at_ms < int(time.time() * 1000):
            raise TokenError("expired launch token")
        return lt


class SessionStore:
    def create(self, player_id: str, room_id: str, game_id: str) -> Session:
        raise NotImplementedError

    def get(self, session_id: str) -> Optional[Session]:
        raise NotImplementedError

    def touch(self, session_id: str, snapshot: Optional[dict] = None) -> Optional[Session]:
        raise NotImplementedError

    def end(self, session_id: str) -> None:
        raise NotImplementedError


class MemorySessionStore(SessionStore):
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._seq = 0

    def create(self, player_id: str, room_id: str, game_id: str) -> Session:
        self._seq += 1
        now = int(time.time() * 1000)
        s = Session(f"sess-{self._seq}-{secrets.token_hex(4)}", player_id,
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