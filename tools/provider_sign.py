#!/usr/bin/env python3
"""Sign a provider request and print a ready-to-run curl command.

Stdlib only, no dependencies:

  python3 tools/provider_sign.py health
  python3 tools/provider_sign.py GET /api/v1/games
  python3 tools/provider_sign.py POST /api/v1/sessions \
      --body '{"player_id":"player_10025","game_code":"teen_patti_pro"}'
  python3 tools/provider_sign.py POST /api/v1/wallet/debit \
      --idempotency-key round_1_bet_1 \
      --body '{"player_id":"player_10025","amount":200,"reference":"round_1_bet_1"}'

Credentials come from PROVIDER_API_KEY / PROVIDER_API_SECRET (or PROVIDER_API_KEYS
as key_id:secret pairs) and the host from PROVIDER_BASE_URL.
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from provider.auth import sign_request


def resolve_credentials():
    single_key = os.environ.get("PROVIDER_API_KEY", "")
    single_secret = os.environ.get("PROVIDER_API_SECRET", "")
    if single_key and single_secret:
        return single_key, single_secret
    pairs = os.environ.get("PROVIDER_API_KEYS", "")
    for part in pairs.split(","):
        if ":" in part:
            key_id, secret = part.split(":", 1)
            if key_id.strip() and secret.strip():
                return key_id.strip(), secret.strip()
    return single_key, single_secret


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("method_or_path", nargs="?", default="GET")
    parser.add_argument("path", nargs="?", default=None)
    parser.add_argument("--body", default=None, help="JSON body")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--raw", action="store_true", help="print headers only")
    args = parser.parse_args()

    if args.path is None:
        method, path = "GET", args.method_or_path
    else:
        method, path = args.method_or_path.upper(), args.path
    if not path.startswith("/"):
        path = "/" + path

    key_id, secret = resolve_credentials()
    if not key_id or not secret:
        parser.error("set PROVIDER_API_KEY and PROVIDER_API_SECRET "
                     "(or PROVIDER_API_KEYS=key_id:secret)")
    base = os.environ.get("PROVIDER_BASE_URL", "http://127.0.0.1:5002").rstrip("/")

    body = args.body.encode() if args.body else b""
    timestamp = int(time.time())
    nonce = secrets.token_hex(8)
    signature = sign_request(secret, method, path.split("?", 1)[0], timestamp,
                             nonce, body)

    headers = [
        ("Content-Type", "application/json"),
        ("X-API-Key", key_id),
        ("X-Timestamp", str(timestamp)),
        ("X-Nonce", nonce),
        ("X-Signature", signature),
    ]
    if args.idempotency_key:
        headers.append(("Idempotency-Key", args.idempotency_key))

    if args.raw:
        for name, value in headers:
            print(f"{name}: {value}")
        return 0

    parts = ["curl", "-sS", "-X", method]
    for name, value in headers:
        parts += ["-H", f"{name}: {value}"]
    if body:
        parts += ["--data-binary", body.decode()]
    parts.append(f"{base}{path}")
    print(" ".join(shlex.quote(p) for p in parts))
    canonical = "\n".join([method, path.split("?", 1)[0], str(timestamp), nonce,
                           hashlib.sha256(body).hexdigest()])
    print(f"\n# canonical string signed:\n# {canonical!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
