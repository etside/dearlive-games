"""Redis-backed staging operator API keys.

These credentials are deliberately narrow:

* staging/demo only (`APP_ENV == "staging"`, never production);
* the games named at issuance, never a wildcard beyond the three V1 games;
* short-lived (15 minutes to 30 days, default 24 hours);
* revocable, rotated, audited, and metadata-only after issuance.

The raw provider secret is returned exactly once, in the issuance response.
List/revoke responses, health checks, logs, and audit rows carry only key
metadata. The separate staging PIN is checked against
`STAGING_PROVISION_PIN`; it is never stored in git and must never be reused as
a production credential.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Dict, List, Optional, Tuple

from provider.games import BINDINGS, canonical_code

KEY_PREFIX = "stg:apikey:"
SECRET_SUFFIX = ":secret"
INDEX_KEY = "stg:apikey:index"
PIN_ATTEMPT_KEY = "stg:apikey:pin-attempts"
ALLOWED_ROLES = ("operator", "auditor")
KEY_ID_RE = "^[A-Za-z0-9._-]{1,64}$"
MIN_TTL_SECONDS = 15 * 60
MAX_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_TTL_SECONDS = 24 * 60 * 60
REVOKE_TOMBSTONE_SECONDS = 7 * 24 * 60 * 60


class DynamicKeyError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


def _utc_ms(now_ms: Optional[int] = None) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


def _app_env() -> str:
    return os.environ.get("APP_ENV", "sandbox").strip().lower()


def _expected_pin() -> str:
    return os.environ.get("STAGING_PROVISION_PIN", "")


def _pin_limits() -> Tuple[int, int]:
    try:
        limit = int(os.environ.get("STAGING_PIN_ATTEMPT_LIMIT", "30"))
    except ValueError:
        limit = 30
    try:
        window = int(os.environ.get("STAGING_PIN_ATTEMPT_WINDOW_SECONDS", "600"))
    except ValueError:
        window = 600
    return max(1, limit), max(60, window)


def _meta_key(key_id: str) -> str:
    return f"{KEY_PREFIX}{key_id}"


def _secret_key(key_id: str) -> str:
    return f"{KEY_PREFIX}{key_id}{SECRET_SUFFIX}"


def _validate_label(label: object) -> str:
    text = str(label or "").strip()
    if not text:
        raise DynamicKeyError("VALIDATION_ERROR", "label is required")
    if len(text) > 64:
        raise DynamicKeyError("VALIDATION_ERROR", "label is too long")
    return text


def _validate_role(role: object) -> str:
    text = str(role or "operator").strip().lower()
    if text not in ALLOWED_ROLES:
        raise DynamicKeyError(
            "VALIDATION_ERROR",
            f"role must be one of: {', '.join(ALLOWED_ROLES)}",
        )
    return text


def _validate_games(games: object) -> List[str]:
    if isinstance(games, str):
        raw = [part.strip() for part in games.split(",")]
    elif isinstance(games, (list, tuple)):
        raw = [str(part).strip() for part in games]
    else:
        raise DynamicKeyError("VALIDATION_ERROR", "games must be a list")
    normalized = []
    for value in raw:
        if not value:
            continue
        code = canonical_code(value)
        if code is None or code not in BINDINGS:
            raise DynamicKeyError(
                "VALIDATION_ERROR",
                f"unknown game in scope: {value}",
            )
        if code not in normalized:
            normalized.append(code)
    if not normalized:
        raise DynamicKeyError("VALIDATION_ERROR", "at least one game is required")
    return normalized


def _validate_ttl(ttl_seconds: object) -> int:
    if ttl_seconds is None or ttl_seconds == "":
        return DEFAULT_TTL_SECONDS
    if isinstance(ttl_seconds, bool):
        raise DynamicKeyError("VALIDATION_ERROR", "ttl_seconds must be an integer")
    try:
        value = int(ttl_seconds)
    except (TypeError, ValueError):
        raise DynamicKeyError("VALIDATION_ERROR", "ttl_seconds must be an integer")
    if value < MIN_TTL_SECONDS or value > MAX_TTL_SECONDS:
        raise DynamicKeyError(
            "VALIDATION_ERROR",
            f"ttl_seconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}",
        )
    return value


def check_pin(redis, supplied_pin: object) -> None:
    """Validate the staging issuance PIN without revealing why it failed."""
    if _app_env() != "staging":
        raise DynamicKeyError("FORBIDDEN", "staging-only", 403)
    expected = _expected_pin()
    if not expected:
        raise DynamicKeyError(
            "INTERNAL_ERROR",
            "staging key provisioning is not configured",
            503,
        )
    limit, window = _pin_limits()
    attempts = redis.command("INCR", PIN_ATTEMPT_KEY)
    try:
        attempts = int(attempts)
    except (TypeError, ValueError):
        attempts = limit + 1
    if attempts == 1:
        redis.command("EXPIRE", PIN_ATTEMPT_KEY, str(window))
    if attempts > limit:
        raise DynamicKeyError("RATE_LIMITED", "too many PIN attempts", 429)
    if not hmac.compare_digest(str(supplied_pin or ""), expected):
        raise DynamicKeyError("UNAUTHENTICATED", "invalid PIN", 401)


def _store_new_key(redis, meta: dict, secret: str, ttl_seconds: int) -> bool:
    meta_json = json.dumps(meta, separators=(",", ":"))
    if (redis.command("SET", _meta_key(meta["key_id"]), meta_json,
                      "NX", "EX", str(ttl_seconds)) != "OK"):
        return False
    if (redis.command("SET", _secret_key(meta["key_id"]), secret,
                      "NX", "EX", str(ttl_seconds)) != "OK"):
        redis.command("DEL", _meta_key(meta["key_id"]))
        return False
    redis.command("SADD", INDEX_KEY, meta["key_id"])
    redis.command("EXPIRE", INDEX_KEY, str(ttl_seconds))
    return True


def issue_provider_key(redis, *, pin: object, label: object,
                       role: object = "operator", games: object = None,
                       ttl_seconds: object = DEFAULT_TTL_SECONDS,
                       now_ms: Optional[int] = None, audit=None) -> dict:
    """Mint a one-time staging provider key and return its only secret copy."""
    check_pin(redis, pin)
    clean_label = _validate_label(label)
    clean_role = _validate_role(role)
    clean_games = _validate_games(games)
    ttl = _validate_ttl(ttl_seconds)
    created = _utc_ms(now_ms)
    expires = created + ttl * 1000
    for _ in range(3):
        key_id = f"stg_{secrets.token_hex(16)}"
        secret = f"stgsk_{secrets.token_hex(32)}"
        meta = {
            "key_id": key_id,
            "label": clean_label,
            "role": clean_role,
            "games": clean_games,
            "environment": "staging",
            "test_only": True,
            "revoked": False,
            "created_at_ms": created,
            "expires_at_ms": expires,
        }
        if _store_new_key(redis, meta, secret, ttl):
            if audit is not None:
                audit("staging-pin", "staging.api-key.issue", "api-key", key_id,
                      after={k: meta[k] for k in
                             ("label", "role", "games", "environment",
                              "test_only", "expires_at_ms")})
            return {"key_id": key_id, "key_secret": secret, **meta}
    raise DynamicKeyError("INTERNAL_ERROR", "could not mint a unique key", 500)


def _read_record(redis, key_id: str) -> Optional[dict]:
    raw = redis.command("GET", _meta_key(key_id))
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def list_provider_keys(redis, *, now_ms: Optional[int] = None) -> List[dict]:
    """Return active and revoked-but-unexpired metadata, never secrets."""
    if _app_env() != "staging":
        raise DynamicKeyError("FORBIDDEN", "staging-only", 403)
    now = _utc_ms(now_ms)
    ids = redis.command("SMEMBERS", INDEX_KEY) or []
    out = []
    for key_id in sorted(set(ids)):
        record = _read_record(redis, str(key_id))
        if record is None:
            redis.command("SREM", INDEX_KEY, str(key_id))
            continue
        if int(record.get("expires_at_ms", 0)) <= now:
            redis.command("DEL", _meta_key(str(key_id)))
            redis.command("DEL", _secret_key(str(key_id)))
            redis.command("SREM", INDEX_KEY, str(key_id))
            continue
        out.append({k: record.get(k) for k in
                    ("key_id", "label", "role", "games", "environment",
                     "test_only", "revoked", "created_at_ms", "expires_at_ms",
                     "revoked_at_ms")})
    return out


def revoke_provider_key(redis, key_id: str, *, actor: str = "staging",
                        now_ms: Optional[int] = None, audit=None) -> bool:
    """Revoke one staging key. Keeps a short tombstone, deletes the secret."""
    if _app_env() != "staging":
        raise DynamicKeyError("FORBIDDEN", "staging-only", 403)
    clean_id = str(key_id or "").strip()
    if not clean_id:
        raise DynamicKeyError("VALIDATION_ERROR", "key_id is required")
    record = _read_record(redis, clean_id)
    if record is None:
        return False
    now = _utc_ms(now_ms)
    remaining = max(0, int(record.get("expires_at_ms", now)) - now)
    tombstone = max(60, min(remaining // 1000, REVOKE_TOMBSTONE_SECONDS))
    record.update({"revoked": True, "revoked_at_ms": now,
                   "revoked_by": actor})
    redis.command("SET", _meta_key(clean_id),
                  json.dumps(record, separators=(",", ":")), "EX",
                  str(int(tombstone)))
    redis.command("DEL", _secret_key(clean_id))
    if audit is not None:
        audit(actor, "staging.api-key.revoke", "api-key", clean_id,
              after={"label": record.get("label"), "games": record.get("games"),
                     "revoked": True})
    return True


def rotate_provider_key(redis, old_key_id: str, *, pin: object,
                        ttl_seconds: object = None, now_ms: Optional[int] = None,
                        actor: str = "staging", audit=None) -> dict:
    """Replace one staging key with an equivalent key, then revoke the old one."""
    old = _read_record(redis, str(old_key_id or "").strip())
    if old is None or old.get("revoked"):
        raise DynamicKeyError("NOT_FOUND", "unknown staging API key", 404)
    replacement = issue_provider_key(
        redis, pin=pin, label=old.get("label", "rotated"),
        role=old.get("role", "operator"), games=old.get("games", []),
        ttl_seconds=(DEFAULT_TTL_SECONDS if ttl_seconds in (None, "")
                     else ttl_seconds), now_ms=now_ms, audit=audit)
    revoke_provider_key(redis, old.get("key_id", ""), actor=actor,
                        now_ms=now_ms, audit=audit)
    return replacement


def load_provider_signing_keys(redis, base_keys: Optional[dict] = None,
                               now_ms: Optional[int] = None) -> Tuple[dict, dict]:
    """Merge short-lived staging keys into HMAC verification material."""
    secrets = dict(base_keys or {})
    scopes = {}
    if _app_env() != "staging":
        return secrets, scopes
    now = _utc_ms(now_ms)
    for record in list_provider_keys(redis, now_ms=now):
        if record.get("revoked"):
            continue
        secret = redis.command("GET", _secret_key(record["key_id"]))
        if not secret:
            continue
        secrets[record["key_id"]] = secret
        scopes[record["key_id"]] = {
            "role": record.get("role", "operator"),
            "games": list(record.get("games", [])),
        }
    return secrets, scopes
