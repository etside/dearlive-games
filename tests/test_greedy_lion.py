"""Greedy Lion + Monkey Wheel delivery tests (API-level plugins, no mocks).

Greedy Lion: full authoritative flow through the generic WheelService with
its own lion option set (session -> state -> bet -> close -> HMAC result ->
exactly-once settlement -> history/recent/reconnect + conservation).
Monkey Wheel: delivery alias of the monkey wheel engine, exercised end to
end over HTTP. Teen Patti §9 routes: tables state/bets, round result,
history. Asset manifests for all three games.
"""
import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.plugins import catalog, import_builtin_games
from common.wallet import MemoryWallet
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService
from games.wheel_common.configs import baby_king_config, greedy_config, greedy_lion_config
from games.wheel_common.service import ServiceError, WheelService
from integrations.dearlive_mock import (MockDearLiveSessions, MockDearLiveTokens,
                                        MockDearLiveWallet)

ADMIN = {"X-Admin-Key": "dev-admin-key"}


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


class LionServiceFlow(unittest.TestCase):
    def test_lion_full_flow(self):
        svc, w = wheel_svc(greedy_lion_config)
        self.assertEqual(svc.game_id, "greedy-lion")
        self.assertEqual(len(svc.config.options), 5)
        for p in ("a", "b"):
            w.fund(p, 5000)
        sa = svc.open_session(svc.tokens.mint("a", "rl", "greedy-lion").token)
        self.assertEqual(sa["game_id"], "greedy-lion")
        st0 = svc.start_round("rl")
        self.assertEqual(st0["status"], "BETTING_OPEN")
        st = svc.state("rl", "a")
        self.assertTrue(all("multiplier" in o and "hot" in o for o in st["options"]))
        b1 = svc.place_bet("rl", "a", "cub", 100, "lk1")
        b2 = svc.place_bet("rl", "b", "lion_crown", 500, "lk2")
        self.assertNotEqual(b1["bet_id"], b2["bet_id"])
        self.assertEqual(w.get_balance("a").available, 4900)
        r = svc.place_bet("rl", "a", "cub", 100, "lk1")
        self.assertEqual(r["bet_id"], b1["bet_id"])
        self.assertEqual(w.get_balance("a").available, 4900)
        with self.assertRaises(ServiceError):
            svc.place_bet("rl", "a", "nope", 100, "lkx")
        svc.close_betting("rl")
        with self.assertRaises(ServiceError):
            svc.place_bet("rl", "a", "cub", 100, "lklate")
        res = svc.publish_result("rl")
        self.assertIn(res["winning_option_id"], [o.option_id for o in svc.config.options])
        stl = svc.settle("rl")
        for bet_id, row in ((x["bet_id"], x) for x in stl["settlements"]):
            wrow = [e for e in w.ledger if e["ref"] == f"settle:{bet_id}"]
            if row["payout"] > 0:
                self.assertEqual(len(wrow), 1)
        total = sum(w.get_balance(p).available for p in ("a", "b"))
        self.assertEqual(total, 10000 - 600 + sum(x["payout"] for x in stl["settlements"]))
        again = svc.settle("rl")
        self.assertEqual(sum(w.get_balance(p).available for p in ("a", "b")), total)
        self.assertEqual(svc.recent_results("rl")["results"][0]["round_id"], stl["round_id"])
        self.assertEqual(len(svc.history("rl", "a")["bets"]), 1)
        rec = svc.reconnect(sa["session_id"], 0)
        self.assertIn("snapshot", rec)
        acts = {e["action"] for e in svc.audit.entries}
        self.assertTrue({"session.open", "round.start", "bet.place",
                         "result.publish", "settlement.credit"} <= acts)


class LionMonkeyHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        w = MemoryWallet()
        w.fund("u1", 20000)
        Handler.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=w)
        cls.gsvc, cls.gw = wheel_svc(greedy_config)
        cls.lsvc, cls.lw = wheel_svc(greedy_lion_config)
        cls.ksvc, cls.kw = wheel_svc(baby_king_config)
        for svc_w in (cls.gw, cls.lw, cls.kw):
            svc_w.fund("u1", 20000)
        Handler.wheels = {"greedy-monkey": cls.gsvc, "greedy-lion": cls.lsvc,
                          "baby-king": cls.ksvc}
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        import_builtin_games()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def sess(self, svc, player, room, game):
        st, body = call("POST", self.base + f"/api/v1/games/{game}/sessions",
                        {"launch_token": svc.tokens.mint(player, room, game).token})
        self.assertEqual(st, 200, body)
        return body["data"]["session_id"]

    def auth(self, sid):
        return {"Authorization": f"Bearer {sid}"}

    def test_catalog_lists_all_three(self):
        st, body = call("GET", self.base + "/api/v1/games")
        self.assertEqual(st, 200)
        ids = {g["game_id"] for g in body["data"]}
        self.assertTrue({"teen-patti-pro", "greedy-lion", "greedy-monkey",
                         "monkey-wheel", "baby-king"} <= ids)

    def test_lion_http_flow(self):
        sid = self.sess(self.lsvc, "u1", "rlion", "greedy-lion")
        st, _ = call("POST", self.base + "/api/v1/games/greedy-lion/rooms/rlion/rounds/start",
                     {}, ADMIN)
        self.assertEqual(st, 200)
        st, body = call("GET", self.base + "/api/v1/games/greedy-lion/rounds/current?room=rlion",
                        headers=self.auth(sid))
        self.assertEqual(st, 200, body)
        self.assertEqual(body["data"]["status"], "BETTING_OPEN")
        opts = {o["option_id"] for o in body["data"]["options"]}
        self.assertTrue({"cub", "lion_crown"} <= opts)
        st, body = call("POST", self.base + "/api/v1/games/greedy-lion/rounds/current/bets?room=rlion",
                        {"option_id": "cub", "amount": 100}, {"Idempotency-Key": "http-lk1",
                                                              **self.auth(sid)})
        self.assertEqual(st, 200, body)
        for op in ("close", "result", "settle"):
            st, _ = call("POST", self.base + f"/api/v1/games/greedy-lion/rooms/rlion/rounds/{op}",
                         {}, ADMIN)
            self.assertEqual(st, 200)
        st, body = call("GET", self.base + "/api/v1/games/greedy-lion/history?room=rlion&limit=10",
                        headers=self.auth(sid))
        self.assertEqual(st, 200)
        self.assertEqual(len(body["data"]["bets"]), 1)
        st, body = call("GET", self.base + "/api/v1/games/greedy-lion/results/recent?room=rlion&limit=5",
                        headers=self.auth(sid))
        self.assertEqual(st, 200)
        self.assertEqual(len(body["data"]["results"]), 1)

    def test_monkey_wheel_alias_end_to_end(self):
        sid = self.sess(self.gsvc, "u1", "rmw", "greedy-monkey")
        st, _ = call("POST", self.base + "/api/v1/games/monkey-wheel/rooms/rmw/rounds/start",
                     {}, ADMIN)
        self.assertEqual(st, 200)
        st, body = call("GET", self.base + "/api/v1/games/monkey-wheel/rounds/current?room=rmw",
                        headers=self.auth(sid))
        self.assertEqual(st, 200, body)
        self.assertEqual(body["data"]["status"], "BETTING_OPEN")
        st, body = call("POST", self.base + "/api/v1/games/monkey-wheel/rounds/current/bets?room=rmw",
                        {"option_id": "banana", "amount": 100}, {"Idempotency-Key": "http-mw1",
                                                                 **self.auth(sid)})
        self.assertEqual(st, 200, body)

    def test_asset_manifests_all_three(self):
        sid = self.sess(self.lsvc, "u1", "ra", "greedy-lion")
        st, body = call("GET", self.base + "/api/v1/games/teen-patti-pro/assets",
                        headers=self.auth(sid))
        self.assertEqual(st, 200)
        self.assertIn("cardBack", body["data"]["manifest"]["files"])
        for gid in ("greedy-lion", "monkey-wheel", "baby-king"):
            st, body = call("GET", self.base + f"/api/v1/games/{gid}/assets",
                            headers=self.auth(sid))
            self.assertEqual(st, 200, gid)
            opts = body["data"]["options"]
            self.assertTrue(len(opts) >= 5, gid)
            self.assertTrue(all("icon" in o and "color_hex" in o for o in opts))
        st, body = call("GET", self.base + "/api/v1/games/nope/assets", headers=self.auth(sid))
        self.assertEqual(st, 404)

    def test_teen_patti_section9_routes(self):
        tok = Handler.svc.tokens.mint("u1", "rtp", "teen-patti-pro").token
        st, body = call("POST", self.base + "/api/v1/sessions", {"launch_token": tok})
        self.assertEqual(st, 200, body)
        sid = body["data"]["session_id"]
        st, _ = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/rtp/rounds/start",
                     {}, ADMIN)
        self.assertEqual(st, 200)
        st, body = call("GET", self.base + "/api/v1/games/teen-patti-pro/tables/rtp/state",
                        headers=self.auth(sid))
        self.assertEqual(st, 200, body)
        st, body = call("POST", self.base + "/api/v1/games/teen-patti-pro/tables/rtp/bets",
                        {"position": "A", "amount": 100}, {"Idempotency-Key": "http-tp1",
                                                           **self.auth(sid)})
        self.assertEqual(st, 200, body)
        for op in ("close", "result"):
            st, _ = call("POST", self.base + f"/api/v1/games/teen-patti-pro/rooms/rtp/rounds/{op}",
                         {}, ADMIN)
            self.assertEqual(st, 200)
        st, body = call("GET", self.base + "/api/v1/games/teen-patti-pro/history?room=rtp",
                        headers=self.auth(sid))
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
