"""Launch-token + session interfaces (DearLive WebView pattern).

Proven flow (from APK .env + vendor Leadercc notes):
  host app mints launch token server-side into the SAME Redis the game
  server reads; game validates token (else connection error); WebView
  opens {GAMES_BASE_URL}/<game>/?token=...; session handshake follows.

CLIENT API REQUIRED: prod mint endpoint + Redis schema/key prefix/TTL
live in the closed dearlive-backend. Ship a Redis impl against the
documented contract; keep Memory impl for dev/tests.
"""
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


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
