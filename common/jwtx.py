"""Minimal HS256 JSON Web Tokens on the standard library.

The operator/admin login path needs a signed bearer token. Rather than add a
third-party dependency to an otherwise standard-library server, this module
implements exactly the subset of JWT that the codebase uses: HS256 signing,
verification, and expiry.

Deliberate limitations (this is not a general JWT library):
  * Only ``HS256``. The algorithm is pinned on both encode and decode, so an
    attacker cannot downgrade to ``none`` or substitute an asymmetric alg.
  * No key discovery, no JWKS, no key rotation.
  * No ``aud``/``nbf``/``jti`` validation.

The exception names intentionally match PyJWT so call sites read the same:

    from common import jwtx as jwt
    token = jwt.encode(payload, secret)
    try:
        claims = jwt.decode(token, secret)
    except jwt.ExpiredSignatureError:
        ...
    except jwt.InvalidTokenError:
        ...
"""
import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Iterable

__all__ = ["encode", "decode", "ExpiredSignatureError", "InvalidTokenError",
           "InvalidSignatureError", "ALGORITHM"]

ALGORITHM = "HS256"
_LEEWAY_S = 0  # no clock skew allowance; tokens are minted and checked locally


class InvalidTokenError(Exception):
    """Token is malformed, wrongly signed, or otherwise unusable."""


class InvalidSignatureError(InvalidTokenError):
    """Signature did not match the signing input."""


class ExpiredSignatureError(InvalidTokenError):
    """``exp`` is in the past."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def encode(payload: Dict[str, Any], secret: str,
           algorithm: str = ALGORITHM) -> str:
    """Sign ``payload`` and return a compact JWS.

    ``iat``/``exp`` are *not* injected: the caller decides the lifetime.
    """
    if algorithm != ALGORITHM:
        raise ValueError(f"unsupported algorithm {algorithm!r}")
    if not secret:
        raise ValueError("secret is required")
    header = {"alg": ALGORITHM, "typ": "JWT"}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
        _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input,
                         hashlib.sha256).digest()
    segments.append(_b64url(signature))
    return ".".join(segments)


def decode(token: str, secret: str,
           algorithms: Iterable[str] = (ALGORITHM,),
           verify_exp: bool = True) -> Dict[str, Any]:
    """Verify ``token`` and return its claims.

    Raises ExpiredSignatureError when ``exp`` has passed, otherwise
    InvalidTokenError (InvalidSignatureError is a subclass) on any problem.
    """
    if algorithm_not_allowed(algorithms):
        raise InvalidTokenError("no allowed algorithm")
    if not secret:
        raise ValueError("secret is required")
    if not token or token.count(".") != 2:
        raise InvalidTokenError("token is not a compact JWS")
    header_b64, payload_b64, signature_b64 = token.split(".")

    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception:
        raise InvalidTokenError("malformed header")
    if not isinstance(header, dict):
        raise InvalidTokenError("malformed header")
    alg = header.get("alg")
    if alg != ALGORITHM:
        # Refuse "none" and any substitution rather than trusting the header.
        raise InvalidTokenError(f"unexpected alg {alg!r}")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        provided = _b64url_decode(signature_b64)
    except Exception:
        raise InvalidTokenError("malformed signature")
    expected = hmac.new(secret.encode("utf-8"), signing_input,
                        hashlib.sha256).digest()
    if not hmac.compare_digest(expected, provided):
        raise InvalidSignatureError("signature mismatch")

    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise InvalidTokenError("malformed payload")
    if not isinstance(claims, dict):
        raise InvalidTokenError("malformed payload")

    if verify_exp:
        exp = claims.get("exp")
        if exp is not None:
            try:
                expired = int(exp) + _LEEWAY_S <= int(time.time())
            except (TypeError, ValueError):
                raise InvalidTokenError("exp is not an integer")
            if expired:
                raise ExpiredSignatureError("signature has expired")

    return claims


def algorithm_not_allowed(algorithms: Iterable[str]) -> bool:
    return ALGORITHM not in set(algorithms or ())
