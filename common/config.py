"""Portable environment-driven settings for DearLive Games.

DearLive owns users + wallet balances. The game server stores ONLY game
records (rounds/bets/settlements/idempotency/webhook-outbox/audit) — never
an independent production money balance.

Environments: sandbox/local vs staging vs production, selected by APP_ENV.
All values come from environment; game logic never hardcodes hosts, ports,
credentials, currency, or user IDs. See .env.example.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, Tuple


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Single source of env config. Construct via Settings.from_env()."""

    app_env: str = "sandbox"  # sandbox | staging | production
    # Game server self
    games_base_url: str = ""  # e.g. https://games.<dearlive-domain> (no default prod)
    api_host: str = "127.0.0.1"
    api_port: int = 5002
    ws_port: int = 5003
    admin_keys: Tuple[Tuple[str, str], ...] = ()  # ((key, role), ...) from GAME_ADMIN_KEYS
    # DearLive upstream (developer-configured)
    dearlive_api_base_url: str = ""
    dearlive_auth_type: str = ""  # Bearer | HMAC | mTLS — as DearLive specifies
    dearlive_client_id: str = ""
    dearlive_client_secret: str = ""
    dearlive_api_key: str = ""
    # Launch-token contract (Redis shared with DearLive host)
    redis_host: str = ""
    redis_port: int = 6379
    redis_db: int = 0
    redis_username: str = ""
    redis_password: str = ""
    redis_tls: bool = False
    token_key_prefix: str = "dearlive:launch:"
    token_ttl_ms: int = 120_000
    # Wallet (DearLive HTTP API; game never stores balances in prod)
    wallet_base_url: str = ""  # falls back to dearlive_api_base_url if empty
    wallet_api_key: str = ""
    wallet_client_id: str = ""
    wallet_client_secret: str = ""
    currency: str = "COIN"  # DearLive coin currency code
    wallet_timeout_ms: int = 8000
    # Webhooks (game -> DearLive)
    webhook_secret: str = ""
    settlement_webhook_url: str = ""
    settlement_signing_secret: str = ""
    webhook_signing_method: str = "HMAC-SHA256"
    # Game-side Postgres (records only, no balances)
    database_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        admin_raw = _get("GAME_ADMIN_KEYS", "")  # "key1:admin,key2:superadmin"
        pairs = []
        for part in admin_raw.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            k, r = part.split(":", 1)
            pairs.append((k.strip(), r.strip()))
        # Back-compat single key
        single = _get("GAME_ADMIN_KEY", "")
        if single:
            pairs.append((single, "admin"))
        return cls(
            app_env=_get("APP_ENV", "sandbox"),
            games_base_url=_get("GAMES_BASE_URL", ""),
            api_host=_get("GAME_API_HOST", "127.0.0.1"),
            api_port=_get_int("GAME_API_PORT", 5002),
            ws_port=_get_int("GAME_WS_PORT", 5003),
            admin_keys=tuple(pairs),
            dearlive_api_base_url=_get("DEARLIVE_API_BASE_URL", ""),
            dearlive_auth_type=_get("DEARLIVE_AUTH_TYPE", ""),
            dearlive_client_id=_get("DEARLIVE_CLIENT_ID", ""),
            dearlive_client_secret=_get("DEARLIVE_CLIENT_SECRET", ""),
            dearlive_api_key=_get("DEARLIVE_API_KEY", ""),
            redis_host=_get("REDIS_HOST", ""),
            redis_port=_get_int("REDIS_PORT", 6379),
            redis_db=_get_int("REDIS_DB", 0),
            redis_username=_get("REDIS_USERNAME", ""),
            redis_password=_get("REDIS_PASSWORD", ""),
            redis_tls=_get_bool("REDIS_TLS", False),
            token_key_prefix=_get("TOKEN_KEY_PREFIX", "dearlive:launch:"),
            token_ttl_ms=_get_int("TOKEN_TTL_MS", 120_000),
            wallet_base_url=_get("WALLET_BASE_URL", _get("DEARLIVE_API_BASE_URL", "")),
            wallet_api_key=_get("WALLET_API_KEY", ""),
            wallet_client_id=_get("WALLET_CLIENT_ID", ""),
            wallet_client_secret=_get("WALLET_CLIENT_SECRET", ""),
            currency=_get("COIN_CURRENCY", _get("WALLET_CURRENCY", "COIN")),
            wallet_timeout_ms=_get_int("WALLET_TIMEOUT_MS", 8000),
            webhook_secret=_get("WEBHOOK_SECRET", ""),
            settlement_webhook_url=_get("SETTLEMENT_WEBHOOK_URL", ""),
            settlement_signing_secret=_get("SETTLEMENT_SIGNING_SECRET", ""),
            webhook_signing_method=_get("WEBHOOK_SIGNING_METHOD", "HMAC-SHA256"),
            database_url=_get("DATABASE_URL", ""),
        )

    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    def validate_for_production(self) -> Dict[str, str]:
        """Return {field: error} for missing prod-critical values (empty = OK)."""
        errors: Dict[str, str] = {}
        if not self.is_production():
            return errors
        required = {
            "games_base_url": self.games_base_url,
            "dearlive_api_base_url": self.dearlive_api_base_url,
            "redis_host": self.redis_host,
            "token_key_prefix": self.token_key_prefix,
            "wallet_base_url": self.wallet_base_url,
            "currency": self.currency,
            "settlement_webhook_url": self.settlement_webhook_url,
            "settlement_signing_secret": self.settlement_signing_secret,
            "database_url": self.database_url,
        }
        for k, v in required.items():
            if not v:
                errors[k] = "required in production (see .env.example)"
        if not self.admin_keys:
            errors["admin_keys"] = "GAME_ADMIN_KEYS required in production"
        return errors
