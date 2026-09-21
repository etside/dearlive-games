"""HTTP stack tests: full REST flow through real sockets (in-process server)."""
import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService

ADMIN = {"X-Admin-Key": "dev-admin-key"}


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        w = MemoryWallet()
        w.fund("alice", 10000)
        w.fund("bob", 10000)
        Handler.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=w)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_full_flow(self):
        # token -> session
        tok = Handler.svc.tokens.mint("alice", "room9", "teen-patti-pro").token
        st, sess = call("POST", self.base + "/api/v1/sessions", {"launch_token": tok})
        self.assertEqual(st, 200, sess)
        S = {"Authorization": "Bearer " + sess["data"]["session_id"]}
        # start round (admin)
        st, r = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/rounds/start",
                     {}, ADMIN)
        self.assertEqual(st, 200, r)
        # bet without idempotency key -> 422/409
        st, b = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/bets",
                     {"position": "A", "amount": 100}, S)
        self.assertIn(st, (409, 422), b)
        # valid bet
        st, b = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/bets",
                     {"position": "A", "amount": 100}, dict(S, **{"Idempotency-Key": "u1"}))
        self.assertEqual(st, 200, b)
        bet_id = b["data"]["bet_id"]
        # duplicate same key+payload -> same bet
        st, b2 = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/bets",
                      {"position": "A", "amount": 100}, dict(S, **{"Idempotency-Key": "u1"}))
        self.assertEqual(b2["data"]["bet_id"], bet_id)
        # state
        st, cur = call("GET", self.base + "/api/v1/games/teen-patti-pro/rounds/current?room=room9",
                       headers=S)
        self.assertEqual(st, 200)
        self.assertEqual(cur["data"]["my_bet"], 100)
        self.assertEqual(cur["data"]["hands"]["A"], ["**"] * 3)  # redacted
        # close + result + settle (admin)
        call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/rounds/close", {}, ADMIN)
        st, res = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/rounds/result",
                       {}, ADMIN)
        self.assertEqual(st, 200, res)
        st, stl = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/rounds/settle",
                       {}, ADMIN)
        self.assertEqual(st, 200, stl)
        # result visible now
        st, res2 = call(
            "GET",
            self.base + "/api/v1/games/teen-patti-pro/rounds/r/result?room=room9",
            headers=S)
        self.assertEqual(st, 200)
        self.assertIn("winners", res2["data"])
        # wallet + history
        st, w = call("GET", self.base + "/api/v1/games/teen-patti-pro/rooms/room9/wallet", headers=S)
        self.assertEqual(st, 200)
        self.assertLessEqual(w["data"]["available"], 10000)
        # admin audit + webhooks + config
        st, a = call("GET", self.base + "/api/v1/admin/audit?limit=5", headers=ADMIN)
        self.assertEqual(st, 200)
        self.assertTrue(len(a["data"]["entries"]) > 0)
        # envelope shape on every response
        for payload in (sess, r, b, cur, res, stl):
            for k in ("success", "code", "message", "data", "serverTime", "requestId"):
                self.assertIn(k, payload)

    def test_unauth(self):
        st, _ = call("GET", self.base + "/api/v1/games/teen-patti-pro/rounds/current?room=x")
        self.assertEqual(st, 401)


if __name__ == "__main__":
    unittest.main()
