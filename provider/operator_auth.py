"""Operator PIN authentication and JWT token management.

Implements Phase 1 of the Auth + API Token + Super Admin control plan.
"""
import os
import json
import time
import secrets
import hashlib
import hmac
import base64
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass
from dataclasses import field
from typing import List

try:
    import bcrypt
    # Test if bcrypt native module works
    bcrypt.checkpw(b'test', b'$2b$10$testtesttesttesttesttestte')
    BCRYPT_AVAILABLE = True
except Exception:
    # Fallback for environments without bcrypt native extension
    # Use a portable hash format: $SH256$rounds$salt$hash
    import hashlib
    import hmac
    import os
    
    class _PortableHash:
        """Portable password hashing that works without native extensions.
        
        Format: $SH256$rounds$salt$hash
        - rounds: iteration count (default 100000)
        - salt: hex-encoded salt
        - hash: hex-encoded PBKDF2-SHA256 hash
        """
        
        @staticmethod
        def hashpw(password: bytes, salt: bytes = None) -> bytes:
            """Hash a password with a salt.
            
            If salt is provided, it should be in format: b'$SH256$rounds$salt'
            If salt is None, a new salt is generated.
            Returns hash in format: b'$SH256$rounds$salt$hash'
            """
            if salt and salt.startswith(b'$SH256$'):
                # Parse existing salt
                parts = salt.decode().split('$')
                rounds = int(parts[2]) if len(parts) > 2 else 100000
                salt_bytes = bytes.fromhex(parts[3]) if len(parts) > 3 else os.urandom(16)
            else:
                rounds = 100000
                salt_bytes = os.urandom(16)
            
            hash_val = hashlib.pbkdf2_hmac('sha256', password, salt_bytes, rounds)
            return f"$SH256${rounds}${salt_bytes.hex()}${hash_val.hex()}".encode()
        
        @staticmethod
        def checkpw(password: bytes, hashed: bytes) -> bool:
            """Verify a password against a hash."""
            if isinstance(hashed, str):
                hashed = hashed.encode()
            if not hashed.startswith(b'$SH256$'):
                return False
            try:
                parts = hashed.decode().split('$')
                if len(parts) != 5:
                    return False
                rounds = int(parts[2])
                salt = bytes.fromhex(parts[3])
                expected_hash = parts[4]
                computed = hashlib.pbkdf2_hmac('sha256', password, salt, rounds).hex()
                return hmac.compare_digest(computed, expected_hash)
            except Exception:
                return False
        
        @staticmethod
        def gensalt(rounds=100000) -> bytes:
            salt = os.urandom(16).hex()
            return f"$SH256${rounds}${salt}".encode()
    
    bcrypt = type('bcrypt', (), {
        'hashpw': staticmethod(_PortableHash.hashpw),
        'checkpw': staticmethod(_PortableHash.checkpw),
        'gensalt': staticmethod(_PortableHash.gensalt),
    })()
    BCRYPT_AVAILABLE = True


@dataclass
class OperatorTokenPayload:
    """JWT payload for operator tokens."""
    scope: str = "operator"
    iat: int = 0
    exp: int = 0
    iss: str = "dearlive-games"
    sub: str = "operator"
    
    def to_dict(self) -> Dict:
        return {
            "scope": self.scope,
            "iat": self.iat,
            "exp": self.exp,
            "iss": self.iss,
            "sub": self.sub,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "OperatorTokenPayload":
        return cls(
            scope=data.get("scope", "operator"),
            iat=data.get("iat", 0),
            exp=data.get("exp", 0),
            iss=data.get("iss", "dearlive-games"),
            sub=data.get("sub", "operator"),
        )


class OperatorAuthError(Exception):
    """Operator authentication error."""
    def __init__(self, message: str, code: str, status: int = 401):
        super().__init__(message)
        self.code = code
        self.status = status


class OperatorAuth:
    """Operator PIN authentication and JWT token management."""
    
    # PIN rate limiting constants
    DEFAULT_PIN_RATE_LIMIT = 5
    DEFAULT_PIN_LOCKOUT_TTL = 3600  # 1 hour in seconds
    DEFAULT_TOKEN_TTL_HOURS = 24
    
    def __init__(self, redis_client=None):
        """Initialize operator auth with optional Redis client for rate limiting.
        
        Args:
            redis_client: Optional Redis client (MinimalRedis or compatible).
                         If None, uses in-memory rate limiting.
        """
        self.redis = None
        self._local_rate_limit: Dict[str, List[float]] = {}
        self._lock = __import__("threading").Lock()
        
        # Load configuration from environment
        self.pin_hash = os.environ.get("OPERATOR_PIN_HASH", "")
        self.token_secret = os.environ.get("OPERATOR_TOKEN_SECRET", "")
        self.token_ttl_hours = int(os.environ.get("OPERATOR_TOKEN_TTL", "24"))
        self.pin_rate_limit = int(os.environ.get("PIN_RATE_LIMIT", "5"))
        self.lockout_ttl = int(os.environ.get("PIN_LOCKOUT_TTL", "3600"))
        
        if self.pin_hash and not BCRYPT_AVAILABLE:
            raise RuntimeError("bcrypt is required but not installed. Run: pip install bcrypt")
        
        # Try to initialize Redis if available
        try:
            from integrations.redis_store import MinimalRedis
            self._redis = MinimalRedis()
            self._has_redis = True
        except Exception:
            self._redis = None
            self._has_redis = False
    
    def _hash_pin(self, pin: str) -> str:
        """Hash a PIN using bcrypt."""
        if not BCRYPT_AVAILABLE:
            raise RuntimeError("bcrypt is required but not installed")
        return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(10)).decode()
    
    def verify_pin(self, pin: str) -> bool:
        """Verify a PIN against the stored hash."""
        if not self.pin_hash:
            raise RuntimeError("OPERATOR_PIN_HASH not configured")
        
        # Use our portable hash verification for $SH256$ format
        if self.pin_hash.startswith('$SH256$'):
            return _PortableHash.checkpw(pin.encode(), self.pin_hash.encode())
        
        # Fall back to bcrypt for legacy hashes
        if BCRYPT_AVAILABLE:
            return bcrypt.checkpw(pin.encode(), self.pin_hash.encode())
        
        raise RuntimeError("Cannot verify PIN: unsupported hash format")
    
    def _get_redis(self):
        """Get Redis client if available."""
        try:
            from integrations.redis_store import MinimalRedis
            return MinimalRedis()
        except Exception:
            return None
    
    def _get_rate_limit_key(self, ip: str) -> str:
        """Generate rate limit key for IP."""
        return f"rate:pin:{ip}"
    
    def _get_lockout_key(self, ip: str) -> str:
        """Generate lockout key for IP."""
        return f"lock:pin:{ip}"
    
    def _check_lockout(self, ip: str) -> bool:
        """Check if IP is locked out. Returns True if locked."""
        lockout_key = f"lock:pin:{ip}"
        if self._has_redis:
            try:
                from integrations.redis_store import MinimalRedis
                redis = MinimalRedis()
                exists = self._redis.command("EXISTS", f"lock:pin:{self._hash_ip(ip)}")
                return exists == "1"
            except Exception:
                pass
        # Fallback to in-memory
        return False
    
    def _hash_ip(self, ip: str) -> str:
        """Hash IP for rate limiting keys."""
        import hashlib
        return hashlib.sha256(ip.encode()).hexdigest()[:16]
    
    def _check_rate_limit(self, ip: str) -> Tuple[bool, int]:
        """Check rate limit for IP. Returns (allowed, remaining)."""
        if self._has_redis:
            try:
                from integrations.redis_store import MinimalRedis
                redis = MinimalRedis()
                key = f"rate:pin:{self._hash_ip(ip)}"
                count = int(self._redis.command("GET", f"rate:pin:{self._hash_ip(ip)}") or 0)
                return count < 5, max(0, 5 - count)
            except Exception:
                pass
        # Fallback to in-memory
        with self._lock:
            now = time.time()
            hour_ago = time.time() - 3600
            if ip not in self._local_rate_limit:
                self._local_rate_limit[ip] = []
            # Clean old entries
            self._local_rate_limit[ip] = [t for t in self._local_rate_limit[ip] if t > time.time() - 3600]
            return len(self._local_rate_limit[ip]) < 5, max(0, 5 - len(self._local_rate_limit[ip]))
    
    def record_attempt(self, ip: str, success: bool) -> None:
        """Record a PIN attempt (success or failure)."""
        if self._has_redis:
            try:
                from integrations.redis_store import MinimalRedis
                redis = MinimalRedis()
                ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
                key = f"rate:pin:{ip_hash}"
                self._redis.command("INCR", f"rate:pin:{hashlib.sha256(ip.encode()).hexdigest()[:16]}")
                self._redis.command("EXPIRE", f"rate:pin:{hashlib.sha256(ip.encode()).hexdigest()[:16]}", "3600")
                return
            except Exception:
                pass
        # Fallback to in-memory
        with self._lock:
            now = time.time()
            if ip not in self._local_rate_limit:
                self._local_rate_limit[ip] = []
            self._local_rate_limit[ip].append(time.time())
    
    def lockout_ip(self, ip: str) -> None:
        """Lock out an IP after too many failed attempts."""
        if self._has_redis:
            try:
                from integrations.redis_store import MinimalRedis
                redis = MinimalRedis()
                ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
                self._redis.command("SET", f"lock:pin:{hashlib.sha256(ip.encode()).hexdigest()[:16]}", "1", "EX", "3600")
                return
            except Exception:
                pass
        # Fallback - in memory we can't easily persist lockout across restarts
        # but we can at least track it in memory
        pass
    
    def _check_lockout(self, ip: str) -> bool:
        """Check if IP is locked out."""
        if self._has_redis:
            try:
                from integrations.redis_store import MinimalRedis
                redis = MinimalRedis()
                return self._redis.command("EXISTS", f"lock:pin:{hashlib.sha256(ip.encode()).hexdigest()[:16]}") == "1"
            except Exception:
                pass
        return False
    
    def _generate_jwt(self, payload: dict) -> str:
        """Generate a JWT token (simple implementation without external deps)."""
        header = {"alg": "HS256", "typ": "JWT"}
        header_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            self.token_secret.encode(),
            signing_input.encode(),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip('=')
        return f"{header_b64}.{payload_b64}.{signature_b64}"
    
    def _verify_jwt(self, token: str) -> Optional[dict]:
        """Verify a JWT token and return payload if valid."""
        try:
            parts = token.split('.')
            if len(parts) != 3:
                return None
            header_b64, payload_b64, signature_b64 = parts
            signing_input = f"{parts[0]}.{parts[1]}"
            expected_signature = hmac.new(
                self.token_secret.encode(),
                signing_input.encode(),
                hashlib.sha256
            ).digest()
            expected_sig = base64.urlsafe_b64encode(expected_signature).decode().rstrip('=')
            if not hmac.compare_digest(signature_b64, expected_sig):
                return None
            payload = json.loads(base64.urlsafe_b64decode(payload_b64 + '=='))
            if payload.get('exp', 0) < int(time.time()):
                return None
            return payload
        except Exception:
            return None
    
    def authenticate_pin(self, pin: str, client_ip: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Authenticate with PIN. Returns (success, token, error_message)."""
        # Check lockout
        if self._check_lockout(client_ip):
            return False, None, "Too many failed attempts. Try again later."
        
        # Check rate limit
        allowed, remaining = self._check_rate_limit(client_ip)
        if not allowed:
            return False, None, "Rate limit exceeded. Try again later."
        
        # Verify PIN
        try:
            if not self.verify_pin(pin):
                self.record_attempt(client_ip, False)
                return False, None, "Invalid PIN"
        except Exception as e:
            return False, None, str(e)
        
        # Success - reset rate limit for this IP
        self._reset_rate_limit(client_ip)
        
        # Generate token
        now = int(time.time())
        exp = int(time.time()) + (24 * 3600)  # 24 hours
        payload = {
            "scope": "operator",
            "iat": int(time.time()),
            "exp": exp,
            "iss": "dearlive-games",
            "sub": "operator"
        }
        token = self._generate_jwt({
            "scope": "operator",
            "iat": int(time.time()),
            "exp": exp,
            "iss": "dearlive-games",
            "sub": "operator"
        })
        
        return True, token, None
    
    def verify_token(self, token: str) -> Optional[dict]:
        """Verify an operator JWT token."""
        return self._verify_jwt(token)
    
    def _reset_rate_limit(self, ip: str) -> None:
        """Reset rate limit for an IP after successful auth."""
        if self._has_redis:
            try:
                from integrations.redis_store import MinimalRedis
                redis = MinimalRedis()
                ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
                self._redis.command("DEL", f"rate:pin:{hashlib.sha256(ip.encode()).hexdigest()[:16]}")
                self._redis.command("DEL", f"lock:pin:{hashlib.sha256(ip.encode()).hexdigest()[:16]}")
            except Exception:
                pass
        with self._lock:
            if ip in self._local_rate_limit:
                del self._local_rate_limit[ip]


# Global operator auth instance (lazy initialization)
_operator_auth: Optional[OperatorAuth] = None


def get_operator_auth() -> OperatorAuth:
    """Get or create the global operator auth instance."""
    global _operator_auth
    if _operator_auth is None:
        _operator_auth = OperatorAuth()
    return _operator_auth


# Convenience functions for use in handlers
def verify_operator_token(token: str) -> Optional[dict]:
    """Verify an operator JWT token."""
    auth = get_operator_auth()
    return auth.verify_token(token)


def require_operator_auth(headers: Dict) -> Optional[dict]:
    """Extract and verify operator token from Authorization header."""
    auth_header = headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]  # Remove "Bearer "
    return verify_operator_token(token)