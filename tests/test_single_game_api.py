"""Single-game HTTP API contract tests.

This used to be the cross-game suite: it ran the same generic bet/round flow
against Teen Patti Pro plus Greedy Monkey and Baby King. Those two engines are
retired, so what remains is the Teen Patti contract -- the ``/tables`` surface
and the admin config route -- plus explicit coverage that a retired game code
now fails cleanly instead of reaching for an engine that no longer exists.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from common.wallet import MemoryWallet
from games.teen_patti_pro import api as api_mod
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService

SUPER = {"X-Admin-Key": "x-sup"}


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:
            return e.code, None


class SingleGameApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # ADMIN_KEYS/ADMIN_SCOPES are module-level state shared by every test in
        # the process. Narrowing them without restoring made later modules fail
        # with spurious 403s, so save and put them back in tearDownClass.
        cls._orig_keys = dict(api_mod.ADMIN_KEYS)
        cls._orig_scopes = dict(getattr(api_mod, "ADMIN_SCOPES", {}))
        api_mod.ADMIN_KEYS.clear()
        api_mod.ADMIN_KEYS.update({"x-sup": "superadmin"})
        w = MemoryWallet()
        w.fund("g1", 20000)
        Handler.svc = TeenPattiService(
            config=TeenPattiConfig(confirmed=True), wallet=w)
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
        api_mod.ADMIN_KEYS.clear()
        api_mod.ADMIN_KEYS.update(cls._orig_keys)
        if hasattr(api_mod, "ADMIN_SCOPES"):
            api_mod.ADMIN_SCOPES.clear()
            api_mod.ADMIN_SCOPES.update(cls._orig_scopes)

    def _session(self, player="g1", room="t1", game="teen-patti-pro"):
        tok = Handler.svc.tokens.mint(player, room, game).token
        st, s = call("POST", self.base + "/api/v1/sessions", {"launch_token": tok})
        self.assertEqual(st, 200, s)
        return {"Authorization": "Bearer " + s["data"]["session_id"]}

    def test_tables_api_bet_and_state(self):
        S = self._session()
        call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/t1/rounds/start",
             {}, SUPER)
        st, cur = call("GET",
                       self.base + "/api/v1/games/teen-patti-pro/rounds/current?room=t1",
                       headers=S)
        rid = cur["data"]["round_id"]
        st, b = call("POST", self.base + "/api/v1/games/teen-patti-pro/tables/t1/bets",
                     {"roundId": rid, "position": "B", "amount": 500,
                      "idempotencyKey": "tbl1"}, S)
        self.assertEqual(st, 200, b)
        st, stt = call("GET",
                       self.base + "/api/v1/games/teen-patti-pro/tables/t1/state",
                       headers=S)
        self.assertEqual(st, 200, stt)
        for key in ("players", "pots", "betting_end_at", "my_bet"):
            self.assertIn(key, stt["data"])

    def test_tables_bet_rejects_a_stale_round_id(self):
        S = self._session(player="g1", room="t2")
        call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/t2/rounds/start",
             {}, SUPER)
        st, b = call("POST", self.base + "/api/v1/games/teen-patti-pro/tables/t2/bets",
                     {"roundId": "stale", "position": "B", "amount": 500,
                      "idempotencyKey": "tbl-stale"}, S)
        self.assertEqual(st, 409, b)

    def test_unknown_game_is_404(self):
        st, _ = call("GET", self.base + "/api/v1/games/nope/rounds/current?room=x",
                     headers={"Authorization": "Bearer z"})
        self.assertEqual(st, 404)

    def test_retired_games_are_404_not_a_crash(self):
        """Greedy Monkey and Baby King are gone from the provider bindings.

        Their codes must resolve to None and produce an ordinary 404, not a
        500 from reaching for an engine that no longer exists.
        """
        from provider.games import canonical_code
        for raw in ("monkey_wheel", "greedy-monkey", "baby_king", "baby-king",
                    "babyking", "greedy_lion"):
            self.assertIsNone(canonical_code(raw), raw)
        S = self._session(player="g1", room="t3")
        for slug in ("greedy-monkey", "baby-king", "monkey-wheel"):
            st, _ = call("GET",
                         f"{self.base}/api/v1/games/{slug}/rounds/current?room=t3",
                         headers=S)
            self.assertEqual(st, 404, slug)

    def test_inventory_lists_only_the_shipped_game(self):
        st, body = call("GET", self.base + "/api/v1/admin/games", headers=SUPER)
        self.assertEqual(st, 200, body)
        entries = body["data"]
        self.assertIsInstance(entries, list)
        ids = [g["game_id"] for g in entries]
        self.assertIn("teen-patti-pro", ids)
        for retired in ("greedy-monkey", "baby-king"):
            self.assertNotIn(retired, ids)

    def test_admin_config_write_is_audited(self):
        st, body = call("PUT",
                        self.base + "/api/v1/admin/games/teen-patti-pro/config",
                        {"enabled": False, "reason": "maintenance window"}, SUPER)
        self.assertEqual(st, 200, body)
        entries = Handler.svc.audit.list("game", 5)
        updates = [e for e in entries if e["action"] == "config.update"]
        self.assertTrue(updates)
        self.assertEqual(updates[-1]["actor"], "superadmin")
        self.assertEqual(updates[-1]["after"].get("reason"), "maintenance window")
        st, _ = call("PUT",
                     self.base + "/api/v1/admin/games/teen-patti-pro/config",
                     {"enabled": True, "reason": "restore"}, SUPER)
        self.assertEqual(st, 200)

    def test_provider_codes_expose_one_game(self):
        from provider.games import game_codes
        self.assertEqual(game_codes(), ["teen_patti_pro"])


if __name__ == "__main__":
    unittest.main()
