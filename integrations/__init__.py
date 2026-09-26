"""Adapter factory: sandbox (memory/mock) vs staging/production (real).

Sandbox (no DEARLIVE/REDIS env set): memory + DearLive-shaped mocks —
local dev/tests only, never production money.

Staging/production: HTTP DearLive wallet + Redis token/session/idempotency
stores, all configured via env (common/config.py). Game logic is identical;
only the adapters change.
"""
import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def build_stores():
    """Return (wallet, tokens, sessions, idempotency, note)."""
    from common.idempotency import MemoryIdempotencyStore
    from common.session import MemorySessionStore, MemoryTokenStore

    # DEMO_MODE forces every in-memory store regardless of what is in the
    # environment. Without this, a REDIS_HOST left over in the shell would
    # quietly make "the zero-dependency demo" depend on Redis -- and fail on a
    # laptop that does not have it running.
    if _env("DEMO_MODE", "").strip().lower() in ("1", "true", "yes"):
        from integrations.dearlive_mock import (MockDearLiveSessions,
                                                MockDearLiveTokens,
                                                MockDearLiveWallet)
        return (MockDearLiveWallet(), MockDearLiveTokens(),
                MockDearLiveSessions(), MemoryIdempotencyStore(),
                "demo(in-memory)")
    use_redis = bool(_env("REDIS_HOST", ""))
    use_http_wallet = bool(_env("WALLET_BASE_URL", _env("DEARLIVE_API_BASE_URL", "")))
    if not use_redis and not use_http_wallet:
        from integrations.dearlive_mock import (MockDearLiveSessions,
                                                MockDearLiveTokens,
                                                MockDearLiveWallet)
        return (MockDearLiveWallet(), MockDearLiveTokens(),
                MockDearLiveSessions(), MemoryIdempotencyStore(),
                "sandbox(mock)")
    wallet, tokens, sessions, idem = None, None, None, None
    if use_http_wallet:
        from integrations.dearlive import HttpDearLiveWallet
        wallet = HttpDearLiveWallet()
    else:
        from integrations.dearlive_mock import MockDearLiveWallet
        wallet = MockDearLiveWallet()
    if use_redis:
        from integrations.redis_store import (RedisIdempotencyStore,
                                              RedisSessionStore,
                                              RedisTokenStore)
        tokens, sessions, idem = (RedisTokenStore(), RedisSessionStore(),
                                  RedisIdempotencyStore())
    else:
        tokens, sessions, idem = (MemoryTokenStore(), MemorySessionStore(),
                                  MemoryIdempotencyStore())
    return wallet, tokens, sessions, idem, "dearlive-backed"
