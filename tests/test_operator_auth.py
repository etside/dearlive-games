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

from provider.operator_auth import PinAuth, _hash_pin


class OperatorAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from integrations.redis_store import MinimalRedis
        cls.redis = MinimalRedis()
        cls.operator_pin = secrets.token_urlsafe(18)
        cls.superadmin_pin = secrets.token_urlsafe(18)
        os.environ["OPERATOR_PIN_HASH"] = _hash_pin(cls.operator_pin)
        os.environ["SUPERADMIN_PIN_HASH"] = _hash_pin(cls.superadmin_pin)
        os.environ["OPERATOR_TOKEN_SECRET"] = secrets.token_hex(32)
        os.environ["SUPERADMIN_TOKEN_SECRET"] = secrets.token_hex(32)
        os.environ["OPERATOR_TOKEN_TTL"] = "24h"
        os.environ["PIN_RATE_LIMIT"] = "5"
        os.environ["PIN_LOCKOUT_TTL"] = "1h"

    @classmethod
    def tearDownClass(cls):
        for pattern in ("rate:pin:*", "lock:pin:*"):
            for key in cls.redis.command("KEYS", pattern) or []:
                cls.redis.command("DEL", key)
        cls.redis.command("DEL", "operator_access_log")
        cls.redis.command("DEL", "operator_access_log:ids")

    def setUp(self):
        for pattern in ("rate:pin:*", "lock:pin:*"):
            for key in self.redis.command("KEYS", pattern) or []:
                self.redis.command("DEL", key)
        self.ip = f"198.51.100.{secrets.randbelow(200) + 1}"
        self.headers = {"X-Forwarded-For": self.ip, "User-Agent": "phase1-test"}

    def call(self, method, path, body=None, token=None, ip=None):
        from staging import wsgi
        data = json.dumps(body).encode() if body is not None else None
        headers = {"X-Forwarded-For": ip or self.ip,
                   "User-Agent": "phase1-test", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        env = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "QUERY_STRING": "", "CONTENT_LENGTH": str(len(data or b"")),
               "CONTENT_TYPE": "application/json", "REMOTE_ADDR": ip or self.ip,
               "wsgi.input": io.BytesIO(data or b""), "wsgi.errors": io.StringIO()}
        for key, value in headers.items():
            env["HTTP_" + key.upper().replace("-", "_")] = value
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = status
            captured["headers"] = response_headers

        payload = b"".join(wsgi.app(env, start_response))
        return int(captured["status"].split()[0]), json.loads(payload or b"null")

    def test_operator_auth_success(self):
        status, body = self.call("POST", "/api/v1/operator/auth", {"pin": self.operator_pin})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["scope"], "operator")
        self.assertTrue(body["operator_token"])
        self.assertGreater(body["expires_at"], time.time())
        rows = [json.loads(item) for item in self.redis.command(
            "LRANGE", "operator_access_log", "0", "20") or []]
        self.assertTrue(any(row.get("pin_status") == "success" and
                            row.get("token_expires_at") == body["expires_at"]
                            for row in rows))

    def test_operator_auth_wrong_pin(self):
        status, body = self.call("POST", "/api/v1/operator/auth", {"pin": "not-the-pin"})
        self.assertEqual(status, 401, body)
        self.assertNotIn(self.operator_pin, json.dumps(body))
        rows = [json.loads(item) for item in self.redis.command(
            "LRANGE", "operator_access_log", "0", "20") or []]
        self.assertTrue(any(row.get("pin_status") == "fail" for row in rows))
        self.assertNotIn(self.operator_pin, json.dumps(rows))

    def test_operator_auth_lockout_after_5_fails(self):
        for _ in range(5):
            status, body = self.call("POST", "/api/v1/operator/auth", {"pin": "not-the-pin"})
            self.assertEqual(status, 401, body)
        status, body = self.call("POST", "/api/v1/operator/auth", {"pin": self.operator_pin})
        self.assertEqual(status, 429, body)
        self.assertEqual(self.redis.command("EXISTS", f"lock:pin:{self.ip}"), 1)

    def test_operator_token_valid(self):
        status, auth = self.call("POST", "/api/v1/operator/auth", {"pin": self.operator_pin})
        self.assertEqual(status, 200, auth)
        status, body = self.call("GET", "/api/v1/operator/sessions",
                                 token=auth["operator_token"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["operator_id"], "global")

    def test_operator_token_expired(self):
        auth = PinAuth("operator", redis_client=self.redis)
        now = int(time.time())
        token = auth._jwt({"scope": "operator", "iat": now - 10,
                           "exp": now - 1, "operator_id": "global"})
        status, body = self.call("GET", "/api/v1/operator/sessions", token=token)
        self.assertEqual(status, 401, body)

    def test_operator_route_requires_token(self):
        status, body = self.call("GET", "/api/v1/operator/sessions")
        self.assertEqual(status, 401, body)
        status, body = self.call("GET", "/api/v1/operator/sessions",
                                 token="not-a-token")
        self.assertEqual(status, 401, body)

    def test_superadmin_auth_success(self):
        status, body = self.call("POST", "/api/v1/superadmin/auth",
                                 {"pin": self.superadmin_pin})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["scope"], "superadmin")
        self.assertTrue(body["superadmin_token"])

    def test_superadmin_route_requires_token(self):
        status, body = self.call("GET", "/api/v1/superadmin/audit")
        self.assertEqual(status, 401, body)
        status, auth = self.call("POST", "/api/v1/superadmin/auth",
                                 {"pin": self.superadmin_pin})
        self.assertEqual(status, 200, auth)
        status, body = self.call("GET", "/api/v1/operator/sessions",
                                 token=auth["superadmin_token"])
        self.assertEqual(status, 401, body)

    def test_rate_limit_key_isolated_per_ip(self):
        first_ip = f"203.0.113.{secrets.randbelow(200) + 1}"
        second_ip = f"203.0.113.{secrets.randbelow(200) + 100}"
        for _ in range(5):
            status, _ = self.call("POST", "/api/v1/operator/auth",
                                  {"pin": "not-the-pin"}, ip=first_ip)
            self.assertEqual(status, 401)
        status, _ = self.call("POST", "/api/v1/operator/auth",
                              {"pin": self.operator_pin}, ip=first_ip)
        self.assertEqual(status, 429)
        status, _ = self.call("POST", "/api/v1/operator/auth",
                              {"pin": "not-the-pin"}, ip=second_ip)
        self.assertEqual(status, 401)
        self.assertEqual(self.redis.command("EXISTS", f"rate:pin:{first_ip}"), 1)
        self.assertEqual(self.redis.command("EXISTS", f"rate:pin:{second_ip}"), 1)


if __name__ == "__main__":
    unittest.main()
