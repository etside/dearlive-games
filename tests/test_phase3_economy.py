import io
import json
import os
import secrets
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ["APP_ENV"] = "staging"

from provider.operator_auth import PinAuth, _hash_pin


class Phase3EconomyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from integrations.redis_store import MinimalRedis
        cls.redis = MinimalRedis()
        os.environ["OPERATOR_PIN_HASH"] = _hash_pin(secrets.token_urlsafe(16))
        os.environ["ADMIN_PIN_HASH"] = _hash_pin(secrets.token_urlsafe(16))
        os.environ["OPERATOR_TOKEN_SECRET"] = secrets.token_hex(32)
        os.environ["ADMIN_TOKEN_SECRET"] = secrets.token_hex(32)
        os.environ["OPERATOR_TOKEN_TTL"] = "24h"
        os.environ["PIN_RATE_LIMIT"] = "5"
        os.environ["PIN_LOCKOUT_TTL"] = "1h"

    def setUp(self):
        for key in self.redis.command("KEYS", "phase3:*") or []:
            self.redis.command("DEL", key)
        self.operator = PinAuth("operator", redis_client=self.redis)
        self.admin_auth = PinAuth("admin", redis_client=self.redis)
        self.operator_token = self.operator._jwt({
            "scope": "operator", "iat": 1, "exp": 9999999999,
            "operator_id": "operator-a"})
        self.admin_token = self.admin_auth._jwt({
            "scope": "admin", "iat": 1, "exp": 9999999999,
            "operator_id": "global"})

    def call(self, method, path, body=None, token=None):
        from staging import wsgi
        raw = json.dumps(body).encode() if body is not None else b""
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "",
               "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json",
               "REMOTE_ADDR": "127.0.0.1", "wsgi.input": io.BytesIO(raw),
               "wsgi.errors": io.StringIO()}
        for key, value in headers.items():
            env["HTTP_" + key.upper().replace("-", "_")] = value
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = status

        payload = b"".join(wsgi.app(env, start_response))
        return int(captured["status"].split()[0]), json.loads(payload or b"null")

    def seed(self, key, rows):
        self.redis.command("DEL", key)
        for row in rows:
            self.redis.command("LPUSH", key, json.dumps(row))

    def test_scope_is_enforced(self):
        status, _ = self.call("GET", "/api/v1/superadmin/dashboard", token=self.operator_token)
        self.assertEqual(status, 401)
        status, _ = self.call("GET", "/api/v1/operator/admin/dashboard", token=self.admin_token)
        self.assertEqual(status, 401)

    def test_empty_dashboards_return_zeroes(self):
        status, body = self.call("GET", "/api/v1/superadmin/dashboard", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["operators"], 0)
        status, body = self.call("GET", "/api/v1/operator/admin/dashboard", token=self.operator_token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["active_games"], 0)
        self.assertEqual(body["data"]["balance"], 0)

    def test_coin_config_ignores_client_operator(self):
        status, body = self.call("PUT", "/api/v1/operator/admin/coin-config", {
            "operator_id": "operator-b", "name": "operator currency",
            "symbol": "op", "coin_to_currency_rate": "2.5",
            "min_purchase": "1", "max_purchase": "100"
        }, self.operator_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["operator_id"], "operator-a")
        status, body = self.call("GET", "/api/v1/operator/admin/coin-config", token=self.operator_token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["coin_to_currency_rate"], "2.5")

    def test_wallet_adjust_is_idempotent_and_audited(self):
        self.seed("phase3:operator:operator-a:players", [{"id": "player-a", "operator_id": "operator-a", "status": "active"}])
        self.seed("phase3:operator:operator-a:wallets", [{"id": "wallet-a", "operator_id": "operator-a", "player_id": "player-a", "balance": "10", "frozen": False}])
        payload = {"amount": "5", "type": "credit", "reason": "test credit", "idempotency_key": "wallet-idem-1"}
        status, first = self.call("POST", "/api/v1/operator/admin/wallets/player-a/adjust", payload, self.operator_token)
        self.assertEqual(status, 200, first)
        status, second = self.call("POST", "/api/v1/operator/admin/wallets/player-a/adjust", payload, self.operator_token)
        self.assertEqual(status, 200, second)
        self.assertEqual(first["data"]["id"], second["data"]["id"])
        self.assertEqual(len(self.redis.command("LRANGE", "phase3:operator:operator-a:transactions", "0", "20") or []), 1)
        self.assertEqual(float(self.call("GET", "/api/v1/operator/admin/wallets/player-a", token=self.operator_token)[1]["data"]["balance"]), 15.0)
        audit = self.redis.command("LRANGE", "phase3:operator:operator-a:audit", "0", "20") or []
        self.assertTrue(any(json.loads(row).get("action") == "wallet.adjust" for row in audit))

    def test_wallet_freeze_is_scoped_and_persisted(self):
        self.seed("phase3:operator:operator-a:wallets", [{"id": "wallet-a", "operator_id": "operator-a", "player_id": "player-a", "balance": "10", "frozen": False}])
        status, body = self.call("POST", "/api/v1/operator/admin/wallets/player-a/freeze", {}, self.operator_token)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["data"]["frozen"])
        self.assertEqual(self.call("GET", "/api/v1/operator/admin/wallets/player-a", token=self.operator_token)[1]["data"]["frozen"], True)

    def test_cross_tenant_player_is_not_visible(self):
        self.seed("phase3:operator:operator-b:players", [{"id": "player-b", "operator_id": "operator-b", "status": "active"}])
        status, _ = self.call("GET", "/api/v1/operator/admin/players/player-b", token=self.operator_token)
        self.assertEqual(status, 404)
        status, _ = self.call("PUT", "/api/v1/operator/admin/players/player-b", {"username": "changed"}, self.operator_token)
        self.assertEqual(status, 404)

    def test_player_ban_is_audited(self):
        self.seed("phase3:operator:operator-a:players", [{"id": "player-a", "operator_id": "operator-a", "status": "active"}])
        status, body = self.call("POST", "/api/v1/operator/admin/players/player-a/ban", {"reason": "policy"}, self.operator_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["status"], "banned")
        audit = self.redis.command("LRANGE", "phase3:operator:operator-a:audit", "0", "20") or []
        self.assertTrue(any(json.loads(row).get("action") == "player.ban" for row in audit))

    def test_device_kick_closes_session(self):
        self.seed("phase3:global:devices", [{"id": "device-a", "operator_id": "operator-a", "status": "active"}])
        status, body = self.call("POST", "/api/v1/superadmin/devices/device-a/kick", {}, self.admin_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["status"], "kicked")
        self.assertIn("ended_at", body["data"])

    def test_locked_field_is_visible_to_operator(self):
        status, field = self.call("POST", "/api/v1/superadmin/locked-fields", {
            "game_slug": "teen-patti-pro", "field_name": "max_bet"
        }, self.admin_token)
        self.assertEqual(status, 201, field)
        status, body = self.call("GET", "/api/v1/operator/admin/dashboard", token=self.operator_token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["locked_fields"][0]["locked"], True)
        self.assertEqual(body["data"]["locked_fields"][0]["editable"], False)

    def test_admin_currency_and_audit(self):
        status, currency = self.call("POST", "/api/v1/superadmin/currencies", {
            "code": "USD", "name": "US Dollar", "symbol": "USD"
        }, self.admin_token)
        self.assertEqual(status, 201, currency)
        status, body = self.call("GET", "/api/v1/superadmin/audit", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(any(row.get("action") == "currency.create" for row in body["data"]["audit"]))

    def test_client_supplied_scope_is_ignored_on_players(self):
        self.seed("phase3:operator:operator-a:players", [{"id": "player-a", "operator_id": "operator-a", "status": "active"}])
        status, body = self.call("PUT", "/api/v1/operator/admin/players/player-a", {
            "operator_id": "operator-b", "username": "scoped"
        }, self.operator_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["operator_id"], "operator-a")
        self.assertEqual(body["data"]["username"], "scoped")


if __name__ == "__main__":
    unittest.main()
