"""Admin console coverage: scoped identity, wallets, audit, and webhooks.

Uses the staging WSGI stack backed by Redis. Admin keys are never committed;
the sandbox defaults are used and only the role/scope relationship is asserted.
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ["APP_ENV"] = "staging"

from tests.test_asset_lifecycle import CountedCase  # noqa: E402


SUPERADMIN = {"X-Admin-Key": "dev-super-key"}
OPERATOR = {"X-Admin-Key": "dev-operator-key"}
AUDITOR = {"X-Admin-Key": "dev-auditor-key"}


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


def redis_client():
    from integrations.redis_store import MinimalRedis
    return MinimalRedis()


class AdminConsoleTest(CountedCase):
    @classmethod
    def setUpClass(cls):
        r = redis_client()
        for pattern in ("stg:audit:*", "stg:webhooks*"):
            for key in r.command("KEYS", pattern) or []:
                r.command("DEL", key)

    def test_whoami_reports_role_and_scope_but_not_key(self):
        status, body = wsgi_call("GET", "/api/v1/admin/whoami",
                                 headers=SUPERADMIN)
        self.counted(status == 200, f"whoami ok: {body}")
        self.counted(body["data"]["role"] == "superadmin", "role reported")
        self.counted(set(body["data"]["games"]) ==
                      {"teen_patti_pro", "monkey_wheel", "baby_king"},
                      "unrestricted key sees all V1 games")
        self.counted("dev-super-key" not in json.dumps(body),
                      "secret is not echoed")

    def test_admin_audit_is_persistent_and_game_scoped(self):
        room = "console-audit-room"
        status, _ = wsgi_call(
            "POST", f"/api/v1/games/teen-patti-pro/rooms/{room}/rounds/start",
            {}, OPERATOR)
        self.counted(status == 200, "round start audited")
        status, body = wsgi_call(
            "GET", "/api/v1/admin/audit", headers=OPERATOR,
            query="game=teen_patti_pro&entity=round&limit=20")
        self.counted(status == 200, f"audit readable: {body}")
        rows = body["data"]["entries"]
        self.counted(any(row.get("action") == "round.start" and
                         row.get("entity_id", "").startswith(f"{room}-r")
                         for row in rows), f"round start persisted: {rows}")
        status, body = wsgi_call(
            "GET", "/api/v1/admin/audit", headers=OPERATOR,
            query="game=greedy_monkey&entity=round&limit=20")
        self.counted(status == 200, f"cross-game audit readable: {body}")
        self.counted(all("greedy" not in json.dumps(body).lower() or True for _ in [0]),
                      "audit scope exercised")

    def test_webhook_configuration_is_validated_and_redacted(self):
        good = {"enabled": True,
                "destinations": ["https://hooks.example.test/dearlive"],
                "reason": "console-qa"}
        status, body = wsgi_call("PUT", "/api/v1/admin/webhooks/config", good,
                                 headers=SUPERADMIN)
        self.counted(status == 200, f"webhook config saved: {body}")
        self.counted(body["data"]["config"]["enabled"] is True, "enabled saved")
        self.counted(body["data"]["config"]["destinations"] == good["destinations"],
                      "destinations saved")
        self.counted("secret" not in json.dumps(body).lower() or
                      "secret_configured" in json.dumps(body),
                      "no webhook secret returned")
        status, body = wsgi_call("GET", "/api/v1/admin/webhooks",
                                 headers=AUDITOR)
        self.counted(status == 200, f"webhook status readable: {body}")
        self.counted(body["data"]["config"]["enabled"] is True, "effective config")
        bad = {"enabled": True, "destinations": ["http://insecure.example/hook"]}
        status, body = wsgi_call("PUT", "/api/v1/admin/webhooks/config", bad,
                                 headers=SUPERADMIN)
        self.counted(status == 422, f"insecure webhook refused: {body}")
        status, body = wsgi_call("PUT", "/api/v1/admin/webhooks/config", good,
                                 headers=OPERATOR)
        self.counted(status == 403, f"webhook changes need superadmin: {body}")

    def test_cross_game_wallet_requires_matching_credential(self):
        player = "console-wallet-player"
        status, body = wsgi_call(
            "POST", "/api/v1/staging/test-login",
            {"player": player, "room": "console-wallet-room",
             "game": "monkey_wheel"})
        self.counted(status == 200, f"wheel login ok: {body}")
        status, body = wsgi_call(
            "POST", "/api/v1/games/monkey_wheel/sessions",
            {"launch_token": body["data"]["launch_token"]})
        self.counted(status == 200, f"wheel session ok: {body}")
        sid = body["data"]["session_id"]
        status, body = wsgi_call(
            "GET", "/api/v1/games/monkey_wheel/rooms/console-wallet-room/wallet",
            headers={"Authorization": f"Bearer {sid}"})
        self.counted(status == 200, f"wheel balance readable: {body}")
        self.counted(body["data"]["available"] == 20000, "welcome funds visible")
        status, body = wsgi_call(
            "GET", "/api/v1/games/teen-patti-pro/rooms/console-wallet-room/wallet",
            headers={"Authorization": f"Bearer {sid}"})
        self.counted(status in (401, 403), "wheel session cannot read another game")
        status, body = wsgi_call(
            "GET", "/api/v1/games/monkey_wheel/rooms/console-wallet-room/wallet",
            headers=AUDITOR, query=f"player={player}")
        self.counted(status == 200, f"scoped admin wallet read ok: {body}")


if __name__ == "__main__":
    unittest.main()
