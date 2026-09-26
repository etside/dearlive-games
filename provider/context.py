"""Provider context wiring.

One factory builds the provider surface for both deployments:
  * standalone / Docker: in-memory nonce, ledger and result stores
  * serverless: Redis-backed equivalents, shared across function instances
The game engine, wallet adapter and audit log are injected by the caller so
the provider layer never reaches for a global.
"""
import os
from typing import Optional

from provider.auth import (MemoryNonceStore, NonceStore, RateLimiter,
                           RedisNonceStore, load_api_keys)
from provider.games import BINDINGS, TEEN_CODE
from provider.ledger import (MemoryLedger, MemoryResultStore, ProviderWallet,
                             RedisLedger, RedisResultStore)
from provider.router import DEFAULT_CLIENT_URL, DEFAULT_SESSION_TTL, ProviderContext
from provider.sessions import (MemorySessionTokenStore, RedisSessionTokenStore)
from provider.tables import TableCatalog

# Default table profile per game. A table id is a room id in every engine.
# Operators can override any of these with PROVIDER_TABLES_<GAME_CODE>.
DEFAULT_TABLES = {
    TEEN_CODE: ("teen-patti-low:Low Stakes:10:100:6:COIN,"
                "teen-patti-mid:Mid Stakes:100:1000:6:COIN,"
                "teen-patti-high:High Stakes:1000:10000:6:COIN"),
}
DEFAULT_CLIENT_PATHS = {
    TEEN_CODE: "/teen-patti-pro/?session=",
}


def _env(name, default=""):
    return os.environ.get(name, default)


def _int_env(name, default):
    try:
        return int(_env(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def public_base_url() -> str:
    return _env("PROVIDER_PUBLIC_BASE_URL", "").rstrip("/")


def build_context(service, wallet_adapter, redis=None, catalog: Optional[TableCatalog] = None,
                  keys=None, currency: str = "", base_url: str = "",
                  client_path: str = "", session_ttl_s: int = 0,
                  audit=None) -> ProviderContext:
    redis = redis if redis is not None else None
    if redis is not None:
        nonces: NonceStore = RedisNonceStore(redis)
        tokens = RedisSessionTokenStore(redis)
        ledger = RedisLedger(redis, cap=_int_env("PROVIDER_LEDGER_CAP", 0))
        results = RedisResultStore(redis)
    else:
        nonces = MemoryNonceStore()
        tokens = MemorySessionTokenStore()
        ledger = MemoryLedger()
        results = MemoryResultStore()
    limiter = RateLimiter(
        limit=_int_env("PROVIDER_RATE_LIMIT", 600),
        window_s=_int_env("PROVIDER_RATE_WINDOW_SECONDS", 60),
        redis=redis)
    currency = (currency or _env("PROVIDER_CURRENCY",
                                 _env("COIN_CURRENCY", "COIN"))).upper()
    wallet = ProviderWallet(
        adapter=wallet_adapter, ledger=ledger, results=results,
        currency=currency,
        idempotency_ttl_s=_int_env("PROVIDER_IDEMPOTENCY_TTL_SECONDS", 86400),
        audit=audit if audit is not None else getattr(service, "audit", None))
    return ProviderContext(
        service=service, wallet=wallet, tokens=tokens,
        catalog=catalog or TableCatalog.from_env(),
        catalogs=build_catalogs(catalog),
        nonces=nonces, limiter=limiter,
        keys=load_api_keys() if keys is None else keys,
        base_url=base_url or public_base_url(),
        currency=currency,
        session_ttl_s=session_ttl_s or _int_env("PROVIDER_SESSION_TTL_SECONDS",
                                                DEFAULT_SESSION_TTL),
        client_path=client_path or _env("PROVIDER_CLIENT_PATH", DEFAULT_CLIENT_URL),
        redis=redis is not None)


def build_catalogs(shared: Optional[TableCatalog] = None) -> dict:
    """One table catalog per game, env-overridable per game code."""
    catalogs = {}
    for code in BINDINGS:
        raw = _env("PROVIDER_TABLES_" + code.upper(), DEFAULT_TABLES.get(code, ""))
        if shared is not None and code == TEEN_CODE and not raw:
            catalogs[code] = shared
        else:
            catalogs[code] = TableCatalog.from_env(raw) if raw else shared
    return catalogs


def client_path_for(code: str) -> str:
    return _env("PROVIDER_CLIENT_PATH_" + code.upper(),
                DEFAULT_CLIENT_PATHS.get(code, DEFAULT_CLIENT_URL))


def staging_context(redis, teen_service, wheels=None):
    """Staging keeps TEST-coin balances in the existing Redis wallet."""
    from staging.redis_wallet import RedisWallet
    from provider.dynamic_keys import load_provider_signing_keys
    ctx = build_context(teen_service, RedisWallet(redis), redis=redis)
    if wheels:
        ctx.attach_games(teen_service, wheels)
    secrets, scopes = load_provider_signing_keys(redis, ctx.keys)
    ctx.keys = secrets
    ctx.key_scopes = scopes
    return ctx


def production_context(service, wallet_adapter=None):
    """Production keeps money at the operator (DearLive) and stores provider
    bookkeeping in Redis. Falls back to memory only when Redis is absent, which
    the caller must treat as a degraded (single-process) deployment."""
    from integrations.dearlive import build_wallet_from_env
    redis = None
    if _env("REDIS_HOST", ""):
        from integrations.redis_store import MinimalRedis
        redis = MinimalRedis()
    return build_context(service, wallet_adapter or build_wallet_from_env(),
                         redis=redis)
