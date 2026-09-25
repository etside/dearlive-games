import io
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ["APP_ENV"] = "staging"
os.environ["DEMO_SESSION_TTL"] = "30m"
os.environ["DEMO_STARTING_BALANCE"] = "10000"


class Phase4DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from integrations.redis_store import MinimalRedis
        cls.redis = MinimalRedis()

    def setUp(self):
        for key in self.redis.command("KEYS", "phase4:*") or []:
            self.redis.command("DEL", key)
        self.ip = "198.51.100.44"

    def call(self, method, path, body=None, token=None, ip=None):
        from staging import wsgi
        raw = json.dumps(body).encode() if body is not None else b""
        headers = {"Content-Type": "application/json", "X-Forwarded-For": ip or self.ip}
        if token:
            headers["X-Demo-Token"] = token
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "",
               "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json",
               "REMOTE_ADDR": ip or self.ip, "wsgi.input": io.BytesIO(raw),
               "wsgi.errors": io.StringIO()}
        for key, value in headers.items():
            env["HTTP_" + key.upper().replace("-", "_")] = value
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        payload = b"".join(wsgi.app(env, start_response))
        return int(captured["status"].split()[0]), json.loads(payload or b"null")

    def create(self, ip=None):
        return self.call("POST", "/api/v1/demo/sessions", {
            "game_slug": "teen-patti-pro", "currency": "USD", "lang": "EN",
            "return_url": "/lobby"
        }, ip=ip)

    def test_demo_session_without_auth_and_token_protected_read(self):
        status, body = self.create()
        self.assertEqual(status, 201, body)
        data = body["data"]
        self.assertTrue(data["session_id"])
        self.assertTrue(data["demo_token"])
        self.assertEqual(data["starting_balance"], "10000")
        status, body = self.call("GET", f"/api/v1/demo/sessions/{data['session_id']}")
        self.assertEqual(status, 401)
        status, body = self.call("GET", f"/api/v1/demo/sessions/{data['session_id']}", token=data["demo_token"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["status"], "active")

    def test_demo_close_and_no_wallet_rows(self):
        _, body = self.create()
        data = body["data"]
        status, closed = self.call("POST", f"/api/v1/demo/sessions/{data['session_id']}/close", token=data["demo_token"])
        self.assertEqual(status, 200, closed)
        self.assertEqual(closed["data"]["status"], "closed")
        self.assertEqual(self.redis.command("KEYS", "phase4:wallet*") or [], [])

    def test_demo_rate_limit_is_per_ip(self):
        for _ in range(5):
            status, _ = self.create()
            self.assertEqual(status, 201)
        status, _ = self.create()
        self.assertEqual(status, 429)
        status, _ = self.create(ip="198.51.100.45")
        self.assertEqual(status, 201)

    def test_demo_ttl_expires(self):
        old = os.environ["DEMO_SESSION_TTL"]
        os.environ["DEMO_SESSION_TTL"] = "1s"
        try:
            status, body = self.create()
            self.assertEqual(status, 201)
            session_id = body["data"]["session_id"]
            time.sleep(1.3)
            status, _ = self.call("GET", f"/api/v1/demo/sessions/{session_id}", token=body["data"]["demo_token"])
            self.assertEqual(status, 404)
        finally:
            os.environ["DEMO_SESSION_TTL"] = old

    def test_landing_and_lobby_contract(self):
        root = Path(__file__).resolve().parents[1]
        landing = (root / "landing/index.html").read_text()
        lobby = (root / "games/teen_patti_pro/client/lobby.html").read_text()
        self.assertIn("Teen Patti Pro", landing)
        self.assertIn("Greedy Monkey", landing)
        self.assertIn("Baby King", landing)
        self.assertIn("meta name=\"description\"", landing)
        self.assertIn("api/v1/demo/sessions", lobby)
        self.assertIn("PLAY DEMO", lobby)
        self.assertNotIn("greedy-lion", lobby)


if __name__ == "__main__":
    unittest.main()
