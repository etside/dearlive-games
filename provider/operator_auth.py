import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

try:
    import bcrypt
except Exception:
    bcrypt = None


class AuthConfigurationError(RuntimeError):
    pass


class AuthError(Exception):
    def __init__(self, message, status, code):
        super().__init__(message)
        self.status = status
        self.code = code


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _parse_ttl(value, default):
    raw = str(value or default).strip().lower()
    if raw.isdigit():
        return int(raw)
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if raw and raw[-1] in units and raw[:-1].replace(".", "", 1).isdigit():
        return int(float(raw[:-1]) * units[raw[-1]])
    return int(default)


def _utc_timestamp(seconds=None):
    value = time.time() if seconds is None else seconds
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _portable_hash(password, salt=None, rounds=120000):
    salt_bytes = os.urandom(16) if salt is None else bytes.fromhex(salt)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, rounds)
    return f"$SH256${rounds}${salt_bytes.hex()}${digest.hex()}"


def _verify_hash(password, encoded):
    if not encoded:
        return False
    if encoded.startswith("$SH256$"):
        try:
            _, _, rounds, salt, expected = encoded.split("$")
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt), int(rounds)
            ).hex()
            return hmac.compare_digest(actual, expected)
        except (TypeError, ValueError):
            return False
    if bcrypt is None:
        return False
    try:
        return bcrypt.checkpw(password.encode(), encoded.encode())
    except (TypeError, ValueError):
        return False


def _hash_pin(password):
    if bcrypt is not None:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    return _portable_hash(password)


def _client_ip(headers, remote_addr=""):
    forwarded = str((headers or {}).get("X-Forwarded-For", "")).split(",")[0].strip()
    return forwarded or str((headers or {}).get("X-Real-IP", "")).strip() or remote_addr or "unknown"


def _country(headers):
    for name in ("CF-IPCountry", "X-Country", "X-Geo-Country"):
        value = str((headers or {}).get(name, "")).strip()
        if value:
            return value[:8]
    return ""


def _device_type(user_agent):
    value = (user_agent or "").lower()
    if "tablet" in value or "ipad" in value:
        return "tablet"
    if "mobile" in value or "android" in value or "iphone" in value:
        return "mobile"
    return "desktop"


def _access_log(redis, ip, user_agent, country, status, expires_at=None):
    row = {
        "id": str(uuid.uuid4()),
        "operator_id": "global",
        "operator_ip": str(ip or "unknown")[:128],
        "device_ua": str(user_agent or "")[:512],
        "device_type": _device_type(user_agent),
        "country": str(country or "")[:8],
        "pin_status": status,
        "created_at": _utc_timestamp(),
        "token_expires_at": expires_at,
    }
    if redis is not None:
        try:
            key = "operator_access_log"
            redis.command("LPUSH", key, json.dumps(row, separators=(",", ":")))
            redis.command("LTRIM", key, "0", "4999")
            redis.command("SADD", key + ":ids", row["id"])
        except Exception:
            pass
    return row


class PinAuth:
    def __init__(self, scope, redis_client=None, remote_addr=""):
        self.scope = scope
        self.redis = redis_client
        self.remote_addr = remote_addr
        self._memory = {}
        self._lock = threading.Lock()
        prefix = "OPERATOR" if scope == "operator" else "SUPERADMIN"
        self.pin_hash = os.environ.get(f"{prefix}_PIN_HASH", "")
        self.token_secret = os.environ.get(f"{prefix}_TOKEN_SECRET", "")
        self.token_ttl = _parse_ttl(os.environ.get("OPERATOR_TOKEN_TTL", "24h"), 86400)
        self.rate_limit = max(1, int(os.environ.get("PIN_RATE_LIMIT", "5") or 5))
        self.lockout_ttl = _parse_ttl(os.environ.get("PIN_LOCKOUT_TTL", "1h"), 3600)
        if not self.pin_hash or not self.token_secret:
            raise AuthConfigurationError(f"{scope} PIN auth is not configured")

    def _key(self, kind, ip):
        return f"{kind}:pin:{ip}"

    def _memory_get(self, key):
        with self._lock:
            value = self._memory.get(key)
            if value and value[0] <= time.time():
                self._memory.pop(key, None)
                return None
            return value

    def _locked(self, ip):
        key = self._key("lock", ip)
        if self.redis is not None:
            try:
                return self.redis.command("EXISTS", key) in (1, True, "1")
            except Exception:
                pass
        return self._memory_get(key) is not None

    def _record_failure(self, ip):
        key = self._key("rate", ip)
        if self.redis is not None:
            try:
                count = int(self.redis.command("INCR", key) or 0)
                if count == 1:
                    self.redis.command("EXPIRE", key, str(self.lockout_ttl))
                if count >= self.rate_limit:
                    self.redis.command(
                        "SET", self._key("lock", ip), "1", "EX", str(self.lockout_ttl)
                    )
                return count, count >= self.rate_limit
            except Exception:
                pass
        with self._lock:
            current = self._memory.get(key)
            count = int(current[1]) if current and current[0] > time.time() else 0
            count += 1
            self._memory[key] = (time.time() + self.lockout_ttl, count)
            if count >= self.rate_limit:
                self._memory[self._key("lock", ip)] = (
                    time.time() + self.lockout_ttl, True
                )
            return count, count >= self.rate_limit

    def _clear_failures(self, ip):
        if self.redis is not None:
            try:
                self.redis.command("DEL", self._key("rate", ip))
                return
            except Exception:
                pass
        with self._lock:
            self._memory.pop(self._key("rate", ip), None)

    def _jwt(self, payload):
        header = _b64encode(json.dumps(
            {"alg": "HS256", "typ": "JWT"}, separators=(",", ":")
        ).encode())
        body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header}.{body}".encode()
        signature = hmac.new(self.token_secret.encode(), signing_input, hashlib.sha256).digest()
        return f"{header}.{body}.{_b64encode(signature)}"

    def authenticate(self, pin, ip, user_agent="", country=""):
        if self._locked(ip):
            _access_log(self.redis, ip, user_agent, country, "locked")
            raise AuthError("too many failed attempts", 429, "PIN_RATE_LIMITED")
        if not _verify_hash(str(pin or ""), self.pin_hash):
            _, locked = self._record_failure(ip)
            _access_log(
                self.redis, ip, user_agent, country, "locked" if locked else "fail"
            )
            if locked:
                raise AuthError("invalid pin", 401, "INVALID_PIN")
            raise AuthError("invalid pin", 401, "INVALID_PIN")
        self._clear_failures(ip)
        now = int(time.time())
        expires_at = now + self.token_ttl
        payload = {
            "scope": self.scope,
            "iat": now,
            "exp": expires_at,
            "iss": "dearlive-games",
            "sub": self.scope,
            "operator_id": "global",
        }
        token = self._jwt(payload)
        _access_log(self.redis, ip, user_agent, country, "success", expires_at)
        return token, payload

    def verify(self, token, required_scope=None):
        try:
            header_b64, payload_b64, signature_b64 = str(token or "").split(".")
            header = json.loads(_b64decode(header_b64))
            if header.get("alg") != "HS256":
                return None
            expected = _b64encode(hmac.new(
                self.token_secret.encode(), f"{header_b64}.{payload_b64}".encode(),
                hashlib.sha256
            ).digest())
            if not hmac.compare_digest(signature_b64, expected):
                return None
            payload = json.loads(_b64decode(payload_b64))
            if int(payload.get("exp", 0)) <= int(time.time()):
                return None
            expected_scope = required_scope or self.scope
            if payload.get("scope") != expected_scope:
                return None
            return payload
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


def _auth_from_headers(headers, scope):
    value = str((headers or {}).get("Authorization", ""))
    if not value.startswith("Bearer "):
        return None
    try:
        auth = PinAuth(scope)
    except AuthConfigurationError:
        return None
    return auth.verify(value[7:], scope)


def require_operator(headers):
    return _auth_from_headers(headers, "operator")


def require_superadmin(headers):
    return _auth_from_headers(headers, "superadmin")
