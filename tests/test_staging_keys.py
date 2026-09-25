"""Staging-only scoped provider API keys.

Covers PIN-gated issuance, superadmin listing/revocation/rotation, per-game
scope enforcement on the same provider contract, and production refusal. The
raw secret is asserted exactly once (at issuance); the list API must never
return it. Requires local Redis, like the existing staging stack tests.
"""
import io
import json
import os
import secrets
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ["APP_ENV"] = "staging"
os.environ["STAGING_PROVISION_PIN"] = "test-pin-8567"
os.environ["STAGING_PIN_ATTEMPT_LIMIT"] = "1000"

from provider import auth as PA  # noqa: E402
from tests.test_asset_lifecycle import CountedCase  # noqa: E402


SUPERADMIN = {"X-Admin-Key": "dev-super-key"}


def wsgi_call(method, path, body=None, headers=None, query=""):
    from staging import wsgi as W
    data = json.dumps(body).encode() if body is not None else None
    env = {"REQUEST_METHOD": method, "PATH_INFO": path,
           "QUERY_STRING": query, "CONTENT_LENGTH": str(len(data or b"")),
           "CONTENT_TYPE": "application/json",
           "wsgi.input": io.BytesIO(data or b""), "wsgi.errors": io.StringIO()}
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    captured = {}

    def start_response(status, rheaders):
        captured["status"] = status
        captured["headers"] = dict(rheaders)

    payload = b"".join(W.app(env, start_response))
    return int(captured["status"].split()[0]), json.loads(payload or b"null")


def signed_call(method, path, key_id, secret, body=None, extra=None):
    raw = json.dumps(body).encode() if body is not None else b""
    timestamp = int(time.time())
    nonce = secrets.token_hex(8)
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": key_id,
        "X-Timestamp": str(timestamp),
        "X-Nonce": nonce,
        "X-Signature": PA.sign_request(secret, method, path, timestamp, nonce, raw),
    }
    headers.update(extra or {})
    return wsgi_call(method, path, json.loads(raw or b"{}") if body is not None else None,
                     headers)


def redis_client():
    from integrations.redis_store import MinimalRedis
    return MinimalRedis()


class StagingKeyManagementTest(CountedCase):
    @classmethod
    def setUpClass(cls):
        r = redis_client()
        for pattern in ("pvdr:apikey*", "stg:audit:provider*", "stg:apikey:pin-attempts"):
            for key in r.command("KEYS", pattern) or []:
                r.command("DEL", key)

    def issue_key(self, label, games, role="operator", ttl_seconds=3600):
        status, body = wsgi_call(
            "POST", "/api/v1/staging/api-keys/provision",
            {"pin": "test-pin-8567", "label": label, "role": role,
             "games": games, "ttl_seconds": ttl_seconds})
        self.counted(status == 201, f"provision 201: {body}")
        data = body["data"]
        self.counted(data["key_id"].startswith("stg_"), "key id scoped")
        self.counted(data["key_secret"].startswith("stgsk_"), "one-time secret shape")
        self.counted(data["role"] == role, "role recorded")
        from provider.games import canonical_code
        self.counted(data["games"] == sorted(canonical_code(g) for g in games),
                     "games normalized to canonical codes")
        self.counted(data["test_only"] is True, "test-only marker")
        self.counted(sum(1 for key in data if key == "key_secret") == 1,
                      "one-time secret is present exactly once")
        return data

    def test_pin_provision_authenticates_and_scopes_one_game(self):
        label = f"qa-monkey-{secrets.token_hex(4)}"
        issued = self.issue_key(label, ["greedy-monkey"])
        status, body = signed_call("GET", "/api/v1/games", issued["key_id"],
                                   issued["key_secret"])
        self.counted(status == 200, f"dynamic key authenticates: {body}")
        codes = [game["game_code"] for game in body["data"]["games"]]
        self.counted(codes == ["monkey_wheel"], f"scoped catalog: {codes}")

        status, body = signed_call(
            "POST", "/api/v1/baby-king/tables/baby-king-low/action",
            issued["key_id"], issued["key_secret"],
            {"action": "bet", "option_id": "teddy", "amount": 20},
            {"Idempotency-Key": f"scope-{secrets.token_hex(4)}"})
        self.counted(status == 403 and body["code"] == "FORBIDDEN",
                      f"out-of-scope game refused before money: {body}")

        status, body = wsgi_call("GET", "/api/v1/staging/api-keys",
                                   headers=SUPERADMIN)
        self.counted(status == 200, f"superadmin lists keys: {body}")
        listed = {row["key_id"]: row for row in body["data"]["keys"]}
        self.counted(issued["key_id"] in listed, "issued key is listed")
        self.counted("key_secret" not in listed[issued["key_id"]],
                      "listing never returns the secret")
        self.counted(listed[issued["key_id"]]["revoked"] is False,
                      "key starts active")

        status, body = wsgi_call(
            "POST", "/api/v1/staging/api-keys/revoke", {"key_id": issued["key_id"]},
            headers=SUPERADMIN)
        self.counted(status == 200 and body["data"]["revoked"] is True,
                      f"superadmin revokes: {body}")
        status, body = signed_call("GET", "/api/v1/games", issued["key_id"],
                                   issued["key_secret"])
        self.counted(status == 401, f"revoked key no longer authenticates: {body}")

    def test_pin_and_validation_fail_closed(self):
        label = f"qa-bad-{secrets.token_hex(4)}"
        status, body = wsgi_call(
            "POST", "/api/v1/staging/api-keys/provision",
            {"pin": "wrong-pin", "label": label, "games": ["teen_patti_pro"]})
        self.counted(status == 401, f"wrong PIN refused: {body}")
        status, body = wsgi_call(
            "POST", "/api/v1/staging/api-keys/provision",
            {"pin": "test-pin-8567", "label": label, "games": ["ludo"]})
        self.counted(status == 422, f"unknown game refused: {body}")
        status, body = wsgi_call(
            "POST", "/api/v1/staging/api-keys/provision",
            {"pin": "test-pin-8567", "label": label, "role": "superadmin",
             "games": ["teen_patti_pro"]})
        self.counted(status == 422, f"admin roles cannot be minted by PIN: {body}")
        status, body = wsgi_call("GET", "/api/v1/staging/api-keys",
                                 headers={"X-Admin-Key": "dev-auditor-key"})
        self.counted(status == 403, f"listing requires superadmin: {body}")

    def test_rotate_replaces_secret_and_revokes_old_key(self):
        label = f"qa-rotate-{secrets.token_hex(4)}"
        old = self.issue_key(label, ["monkey_wheel"])
        status, body = wsgi_call(
            "POST", "/api/v1/staging/api-keys/rotate",
            {"key_id": old["key_id"], "pin": "test-pin-8567"},
            headers=SUPERADMIN)
        self.counted(status == 201, f"rotate 201: {body}")
        new = body["data"]
        self.counted(new["key_id"] != old["key_id"], "key id rotated")
        self.counted(new["key_secret"] != old["key_secret"], "secret rotated")
        self.counted(new["games"] == ["monkey_wheel"], "scope preserved")
        status, _ = signed_call("GET", "/api/v1/games", old["key_id"],
                                old["key_secret"])
        self.counted(status == 401, "old secret stops working after rotation")
        status, body = signed_call("GET", "/api/v1/games", new["key_id"],
                                   new["key_secret"])
        self.counted(status == 200, f"rotated key works: {body}")

    def test_staging_key_routes_are_production_refused(self):
        from staging import wsgi as W
        old = os.environ.get("APP_ENV")
        os.environ["APP_ENV"] = "production"
        try:
            status, _headers, body = W._staging_issue_key(
                redis_client(),
                json.dumps({"pin": "test-pin-8567", "label": "x",
                            "games": ["teen_patti_pro"]}).encode())
            self.counted(status == 403, f"production refused: {body}")
        finally:
            os.environ["APP_ENV"] = old or "staging"


if __name__ == "__main__":
    unittest.main()
