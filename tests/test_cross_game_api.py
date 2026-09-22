"""Cross-game HTTP contract: generic {gameId} routes, tables API, admin config."""
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
from games.wheel_common.configs import baby_king_config, greedy_config
from games.wheel_common.service import WheelService
from integrations.dearlive_mock import (MockDearLiveSessions, MockDearLiveTokens,
                                        MockDearLiveWallet)

ADMIN = {"X-Admin-Key": "dev-admin-key"}
SUPER = {"X-Admin-Key": "dev-super-key"}


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if body is not None and method in ("POST", "PUT"):
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def wheel_svc(cfg_fn):
    cfg = cfg_fn()
    cfg.confirmed = True
    w = MockDearLiveWallet()
    return WheelService(config=cfg, wallet=w, tokens=MockDearLiveTokens(),
                        sessions=MockDearLiveSessions()), w


class CrossGameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        w = MemoryWallet()
        w.fund("g1", 20000)
        Handler.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=w)
        cls.gsvc, cls.gw = wheel_svc(greedy_config)
        cls.ksvc, cls.kw = wheel_svc(baby_king_config)
        for p in ("g1",):
            cls.gw.fund(p, 20000)
            cls.kw.fund(p, 20000)
        Handler.wheels = {"greedy-monkey": cls.gsvc, "baby-king": cls.ksvc}
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def wheel_session(self, game, player, room):
        svc = self.gsvc if "greedy" in game else self.ksvc
        tok = svc.tokens.mint(player, room, game).token
        st, s = call("POST", f"{self.base}/api/v1/games/{game}/sessions",
                     {"launch_token": tok})
        self.assertEqual(st, 200, s)
        return {"Authorization": "Bearer " + s["data"]["session_id"]}

    def test_greedy_generic_flow(self):
        S = self.wheel_session("greedy-monkey", "g1", "gr1")
        st, r = call("POST", f"{self.base}/api/v1/games/greedy-monkey/rooms/gr1/rounds/start",
                     {}, ADMIN)
        self.assertEqual(st, 200, r)
        rid = r["data"]["round_id"]
        st, cur = call("GET", f"{self.base}/api/v1/games/greedy-monkey/rounds/current?room=gr1",
                       headers=S)
        self.assertEqual(st, 200, cur)
        self.assertIn("options", cur["data"])
        self.assertIn("betting_end_at", cur["data"])
        # bets via {roundId} path (spec shape)
        st, b = call("POST", f"{self.base}/api/v1/games/greedy-monkey/rounds/{rid}/bets?room=gr1",
                     {"option_id": "banana", "amount": 100},
                     dict(S, **{"Idempotency-Key": "gx1"}))
        self.assertEqual(st, 200, b)
        self.assertTrue(b["data"]["bet_id"])
        # history + recent (empty pre-settle recent ok)
        st, h = call("GET", f"{self.base}/api/v1/games/greedy-monkey/history?room=gr1", headers=S)
        self.assertEqual(st, 200)
        self.assertEqual(len(h["data"]["bets"]), 1)
        # close/result/settle via generic admin path
        call("POST", f"{self.base}/api/v1/games/greedy-monkey/rooms/gr1/rounds/close", {}, ADMIN)
        st, res = call("POST", f"{self.base}/api/v1/games/greedy-monkey/rooms/gr1/rounds/result",
                       {}, ADMIN)
        self.assertEqual(st, 200, res)
        self.assertIn("winning_option_id", res["data"])
        st, stl = call("POST", f"{self.base}/api/v1/games/greedy-monkey/rooms/gr1/rounds/settle",
                       {}, ADMIN)
        self.assertEqual(st, 200, stl)
        # result + recent visible
        st, rv = call("GET", f"{self.base}/api/v1/games/greedy-monkey/rounds/{rid}/result?room=gr1",
                      headers=S)
        self.assertEqual(st, 200, rv)
        st, rc = call("GET", f"{self.base}/api/v1/games/greedy-monkey/results/recent?room=gr1",
                      headers=S)
        self.assertEqual(st, 200)
        self.assertEqual(rc["data"]["results"][0]["round_id"], rid)

    def test_autoplay_alias_and_autobet(self):
        self.gsvc.config.auto_allowed = True
        S = self.wheel_session("greedy-monkey", "g1", "grA")
        st, a = call("POST", f"{self.base}/api/v1/games/greedy-monkey/autoplay?room=grA",
                     {"option_id": "mango", "amount": 100, "rounds": 1}, S)
        self.assertEqual(st, 200, a)
        st, a = call("POST", f"{self.base}/api/v1/games/baby-king/autobet?room=krA",
                     {"option_id": "lion", "amount": 100, "rounds": 1},
                     self.wheel_session("baby-king", "g1", "krA"))
        self.assertEqual(st, 402 if False else 403, a)  # baby-king auto not allowed
        self.gsvc.config.auto_allowed = False

    def test_tables_api_teen(self):
        tok = Handler.svc.tokens.mint("g1", "t1", "teen-patti-pro").token
        st, s = call("POST", self.base + "/api/v1/sessions", {"launch_token": tok})
        S = {"Authorization": "Bearer " + s["data"]["session_id"]}
        call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/t1/rounds/start", {}, ADMIN)
        st, cur = call("GET", self.base + "/api/v1/games/teen-patti-pro/rounds/current?room=t1",
                       headers=S)
        rid = cur["data"]["round_id"]
        # POST tables bets with roundId + idempotencyKey in body (spec inputs)
        st, b = call("POST", self.base + "/api/v1/games/teen-patti-pro/tables/t1/bets",
                     {"roundId": rid, "position": "B", "amount": 500,
                      "idempotencyKey": "tbl1"}, S)
        self.assertEqual(st, 200, b)
        st, stt = call("GET", self.base + "/api/v1/games/teen-patti-pro/tables/t1/state", headers=S)
        self.assertEqual(st, 200, stt)
        self.assertIn("players", stt["data"])
        self.assertIn("pots", stt["data"])
        self.assertIn("timer" if False else "betting_end_at", stt["data"])
        self.assertIn("my_bet", stt["data"])
        # stale roundId rejected
        st, b = call("POST", self.base + "/api/v1/games/teen-patti-pro/tables/t1/bets",
                     {"roundId": "stale", "position": "B", "amount": 500,
                      "idempotencyKey": "tbl2"}, S)
        self.assertEqual(st, 409, b)

    def test_admin_games_config(self):
        st, g = call("GET", self.base + "/api/v1/admin/games", headers=ADMIN)
        self.assertEqual(st, 200, g)
        ids = {x["game_id"] for x in g["data"]}
        self.assertTrue({"teen-patti-pro", "greedy-monkey", "baby-king"} <= ids)
        st, c = call("GET", self.base + "/api/v1/admin/games/greedy-monkey/config",
                     headers=ADMIN)
        self.assertEqual(st, 200, c)
        self.assertIn("options", c["data"])
        # superadmin update: durations + HOT flag
        opts = c["data"]["options"]
        opts[0]["hot"] = True
        st, u = call("PUT", self.base + "/api/v1/admin/games/greedy-monkey/config",
                     {"betting_duration_ms": 25000, "options": opts}, SUPER)
        self.assertEqual(st, 200, u)
        self.assertEqual(u["data"]["betting_duration_ms"], 25000)
        self.assertTrue(u["data"]["options"][0]["hot"])
        # admin (non-super) forbidden on PUT
        st, _ = call("PUT", self.base + "/api/v1/admin/games/greedy-monkey/config",
                     {"betting_duration_ms": 1}, ADMIN)
        self.assertEqual(st, 403)
        # teen frozen config rejects
        st, _ = call("PUT", self.base + "/api/v1/admin/games/teen-patti-pro/config",
                     {"guess_ms": 1}, SUPER)
        self.assertEqual(st, 422)
        # disable gate (session opened while enabled, then gated)
        S = self.wheel_session("baby-king", "g1", "krX")
        st, _ = call("PUT", self.base + "/api/v1/admin/games/baby-king/config",
                     {"enabled": False}, SUPER)
        self.assertEqual(st, 200)
        st, _ = call("GET", self.base + "/api/v1/games/baby-king/rounds/current?room=krX",
                     headers=S)
        self.assertEqual(st, 403)
        call("PUT", self.base + "/api/v1/admin/games/baby-king/config",
             {"enabled": True}, SUPER)

    def test_unknown_game_404(self):
        st, _ = call("GET", self.base + "/api/v1/games/nope/rounds/current?room=x",
                     headers={"Authorization": "Bearer z"})
        self.assertEqual(st, 404)


if __name__ == "__main__":
    unittest.main()
