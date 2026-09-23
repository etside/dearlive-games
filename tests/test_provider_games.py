"""Multi-game provider coverage and per-game admin scope enforcement.

One credential must serve every game through the same contract, and an admin
key scoped to one game must be refused on the others.
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService
from games.wheel_common.configs import greedy_config, greedy_lion_config
from games.wheel_common.service import WheelService
from provider import auth as PA
from provider.context import build_context
from provider.games import (BINDINGS, LION_CODE, MONKEY_CODE, TEEN_CODE,
                            binding_for_code, binding_for_slug, canonical_code)
from provider.router import dispatch, is_provider_path

API_KEY = "tp_multi"
API_SECRET = "multi-game-secret"
GAMES = [(TEEN_CODE, "teen-patti", "position"),
         (LION_CODE, "greedy-lion", "option_id"),
         (MONKEY_CODE, "monkey-wheel", "option_id")]


def make_context():
    teen = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                            wallet=MemoryWallet())
    wheels = {}
    for mk in (greedy_config, greedy_lion_config):
        cfg = mk()
        cfg.confirmed = True
        wheels[cfg.game_id] = WheelService(config=cfg, wallet=MemoryWallet())
    ctx = build_context(teen, teen.wallet, keys={API_KEY: API_SECRET})
    ctx.attach_games(teen, wheels)
    for service in [teen] + list(wheels.values()):
        service.wallet.fund("player_10025", 50000)
    return ctx, teen, wheels


class GameRegistryTest(unittest.TestCase):
    def test_aliases_resolve_to_canonical_codes(self):
        self.assertEqual(canonical_code("teen-patti-pro"), TEEN_CODE)
        self.assertEqual(canonical_code("TEEN_PATTI_PRO"), TEEN_CODE)
        self.assertEqual(canonical_code("greedy-monkey"), MONKEY_CODE)
        self.assertEqual(canonical_code("monkey_wheel"), MONKEY_CODE)
        self.assertEqual(canonical_code("greedy-lion"), LION_CODE)
        self.assertIsNone(canonical_code("ludo"))

    def test_slugs_are_unique_and_resolvable(self):
        slugs = [b.slug for b in BINDINGS.values()]
        self.assertEqual(len(slugs), len(set(slugs)))
        for code, slug, _ in GAMES:
            self.assertEqual(binding_for_slug(slug).game_code, code)
        self.assertIsNone(binding_for_slug("poker"))

    def test_unknown_game_path_is_not_a_provider_path(self):
        self.assertFalse(is_provider_path("/api/v1/poker/tables"))

    def test_legacy_game_routes_are_not_hijacked(self):
        self.assertFalse(is_provider_path("/api/v1/games/teen-patti-pro/tables/x/state"))


class MultiGameProviderTest(unittest.TestCase):
    def setUp(self):
        self.ctx, self.teen, self.wheels = make_context()

    def call(self, method, path, body=None, extra=None, query=""):
        payload = json.dumps(body).encode() if body is not None else b""
        ts = int(__import__("time").time())
        nonce = os.urandom(8).hex()
        headers = {"X-API-Key": API_KEY, "X-Timestamp": str(ts),
                   "X-Nonce": nonce,
                   "X-Signature": PA.sign_request(API_SECRET, method, path, ts,
                                                  nonce, payload)}
        headers.update(extra or {})
        return dispatch(self.ctx, method, path, query, headers, payload)

    def data(self, result):
        status, _headers, payload = result
        self.assertTrue(payload.get("success"), payload)
        self.assertIn(status, (200, 201, 302), payload)
        return payload.get("data")

    def test_catalog_lists_all_three_games(self):
        games = self.data(self.call("GET", "/api/v1/games"))["games"]
        codes = {g["game_code"] for g in games}
        self.assertEqual(codes, {TEEN_CODE, LION_CODE, MONKEY_CODE})
        for game in games:
            self.assertTrue(game["tables"], game)
            self.assertIn(game["choice_field"], ("position", "option_id"))
            self.assertIn("bet", game["actions"])

    def test_full_round_for_every_game(self):
        for code, slug, field in GAMES:
            with self.subTest(game=code):
                session = self.data(self.call(
                    "POST", "/api/v1/sessions",
                    {"player_id": "player_10025", "game_code": code,
                     "currency": "COIN"}))
                self.assertEqual(session["game_code"], code)
                self.assertTrue(session["session_token"].startswith("gst_"))
                table = session["table_id"]

                choices = self.data(self.call(
                    "GET", f"/api/v1/{slug}/tables/{table}/choices"))
                self.assertEqual(choices["choice_field"], field)
                self.assertTrue(choices["choices"])

                before = None
                result = self.data(self.call(
                    "POST", f"/api/v1/{slug}/tables/{table}/action",
                    {"action": "bet", "session_id": session["session_id"],
                     field: choices["choices"][0]["choice"], "amount": 20},
                    {"Idempotency-Key": f"{code}-bet-1"}))
                self.assertTrue(result["accepted"])
                self.assertEqual(result[field], choices["choices"][0]["choice"])

                state = self.data(self.call(
                    "GET", f"/api/v1/{slug}/tables/{table}/state",
                    query="player_id=player_10025"))
                self.assertEqual(state["state"]["status"], "BETTING_OPEN")

                service = (self.teen if code == TEEN_CODE else
                           self.wheels["greedy-lion" if code == LION_CODE
                                       else "greedy-monkey"])
                service.sweep(int(__import__("time").time() * 1000) + 10 ** 9)
                history = self.data(self.call(
                    "GET", f"/api/v1/{slug}/tables/{table}/history"))
                self.assertGreaterEqual(history["count"], 1, code)
                self.assertEqual(history["game_code"], code)

    def test_bet_replay_does_not_double_debit(self):
        for code, slug, field in GAMES:
            with self.subTest(game=code):
                session = self.data(self.call(
                    "POST", "/api/v1/sessions",
                    {"player_id": "player_10025", "game_code": code,
                     "currency": "COIN"}))
                table = session["table_id"]
                choices = self.data(self.call(
                    "GET", f"/api/v1/{slug}/tables/{table}/choices"))
                body = {"action": "bet", "session_id": session["session_id"],
                        field: choices["choices"][0]["choice"], "amount": 20}
                first = self.data(self.call(
                    "POST", f"/api/v1/{slug}/tables/{table}/action", body,
                    {"Idempotency-Key": f"{code}-replay"}))
                balance = self.data(self.call(
                    "GET", "/api/v1/players/player_10025/balance"))["available"]
                again = self.data(self.call(
                    "POST", f"/api/v1/{slug}/tables/{table}/action", body,
                    {"Idempotency-Key": f"{code}-replay"}))
                self.assertEqual(first["bet_id"], again["bet_id"])
                after = self.data(self.call(
                    "GET", "/api/v1/players/player_10025/balance"))["available"]
                self.assertEqual(after, balance)

    def test_session_is_playable_against_every_game(self):
        """A session id issued by one engine must resolve through the API."""
        for code, slug, field in GAMES:
            with self.subTest(game=code):
                session = self.data(self.call(
                    "POST", "/api/v1/sessions",
                    {"player_id": "player_10025", "game_code": code,
                     "currency": "COIN"}))
                read = self.data(self.call(
                    "GET", f"/api/v1/sessions/{session['session_id']}"))
                self.assertEqual(read["player_id"], "player_10025")
                self.assertEqual(read["table_id"], session["table_id"])
                status, _h, payload = self.call(
                    "GET", f"/api/v1/{slug}/tables/{session['table_id']}/history",
                    query=f"player_id=player_10025")
                self.assertTrue(payload.get("success"), payload)

    def test_join_leave_and_table_full(self):
        session = self.data(self.call(
            "POST", "/api/v1/sessions",
            {"player_id": "player_10025", "game_code": LION_CODE,
             "currency": "COIN", "table_id": "greedy-lion-low"}))
        table = "greedy-lion-low"
        joined = self.data(self.call(
            "POST", f"/api/v1/greedy-lion/tables/{table}/join",
            {"session_id": session["session_id"]}))
        self.assertIn("player_10025", joined["seats"])
        left = self.data(self.call(
            "POST", f"/api/v1/greedy-lion/tables/{table}/leave",
            {"session_id": session["session_id"]}))
        self.assertNotIn("player_10025", left["seats"])
        self.assertTrue(left["removed"])

    def test_fold_and_show_are_refused_for_wheels_too(self):
        session = self.data(self.call(
            "POST", "/api/v1/sessions",
            {"player_id": "player_10025", "game_code": MONKEY_CODE,
             "currency": "COIN"}))
        table = session["table_id"]
        for action in ("fold", "show"):
            status, _h, payload = self.call(
                "POST", f"/api/v1/monkey-wheel/tables/{table}/action",
                {"action": action, "session_id": session["session_id"],
                 "option_id": "any", "amount": 20},
                {"Idempotency-Key": f"wheel-{action}"})
            self.assertEqual(status, 422, payload)
            self.assertIn("not part of", payload["message"])

    def test_launch_redirect_targets_the_right_game(self):
        # The API slug and the frontend route differ by design (teen-patti is
        # served at /teen-patti-pro/), so assert the real client path per game.
        client_paths = {TEEN_CODE: "/teen-patti-pro/",
                        LION_CODE: "/greedy-lion/",
                        MONKEY_CODE: "/monkey-wheel/"}
        for code, _slug, _field in GAMES:
            with self.subTest(game=code):
                session = self.data(self.call(
                    "POST", "/api/v1/sessions",
                    {"player_id": "player_10025", "game_code": code,
                     "currency": "COIN"}))
                status, headers, _payload = self.call(
                    "GET", f"/api/v1/provider/launch/{session['session_token']}")
                self.assertEqual(status, 302)
                self.assertTrue(headers["Location"].startswith(client_paths[code]),
                                headers["Location"])
                self.assertIn("room=", headers["Location"])

    def test_unknown_game_code_is_rejected(self):
        status, _h, payload = self.call(
            "POST", "/api/v1/sessions",
            {"player_id": "player_10025", "game_code": "ludo"})
        self.assertEqual(status, 422)
        self.assertIn("game_code", payload["message"])


class AdminScopeTest(unittest.TestCase):
    """A key scoped to one game must be refused on every other game."""

    @classmethod
    def setUpClass(cls):
        cls.teen = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                                    wallet=MemoryWallet())
        cfg = greedy_lion_config()
        cfg.confirmed = True
        cls.wheel = WheelService(config=cfg, wallet=MemoryWallet())
        Handler.svc = cls.teen
        Handler.wheels = {"greedy-lion": cls.wheel}
        Handler.provider_ctx = None
        Handler.provider_tokens = None
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        import games.teen_patti_pro.api as api_module
        cls.api = api_module
        cls._orig_keys = api_module.ADMIN_KEYS
        cls._orig_scopes = api_module.ADMIN_SCOPES
        api_module.ADMIN_KEYS = {"lion-op": "operator", "all-op": "operator",
                                 "ro": "auditor", "super": "superadmin"}
        api_module.ADMIN_SCOPES = {"lion-op": {"greedy-lion", "greedy_lion"}}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        # Module-level RBAC is shared process state; restore it so this module
        # cannot leak a narrowed key set into other test modules.
        cls.api.ADMIN_KEYS = cls._orig_keys
        cls.api.ADMIN_SCOPES = cls._orig_scopes

    def post(self, path, key):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=b"{}", method="POST",
            headers={"X-Admin-Key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_scoped_key_allowed_on_its_game_and_denied_elsewhere(self):
        room = "scope-room-a"
        status, body = self.post(
            f"/api/v1/games/greedy-lion/rooms/{room}/rounds/start", "lion-op")
        self.assertEqual(status, 200, body)
        status, body = self.post(
            f"/api/v1/games/teen-patti-pro/rooms/{room}/rounds/start", "lion-op")
        self.assertEqual(status, 403, body)
        self.assertIn("not scoped", body["message"])

    def test_unscoped_key_still_works_everywhere(self):
        room = "scope-room-b"
        status, _ = self.post(
            f"/api/v1/games/greedy-lion/rooms/{room}/rounds/start", "all-op")
        self.assertIn(status, (200, 409))
        status, body = self.post(
            f"/api/v1/games/teen-patti-pro/rooms/{room}/rounds/start", "all-op")
        self.assertEqual(status, 200, body)

    def test_scoped_key_still_needs_the_right_role(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}"
            "/api/v1/games/greedy-lion/rooms/scope-room-c/rounds/start",
            data=b"{}", method="POST",
            headers={"X-Admin-Key": "ro", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 403)

    def test_superadmin_config_write_respects_scope(self):
        path = "/api/v1/admin/games/greedy-lion/config"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps({"version": "x"}).encode(), method="PUT",
            headers={"X-Admin-Key": "lion-op", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 403)

    def test_scope_parser_ignores_malformed_entries(self):
        from games.teen_patti_pro.api import _load_admin_scopes
        parsed = _load_admin_scopes("a:greedy-lion,b;,c:lion|monkey,:x")
        self.assertEqual(parsed, {"a": {"greedy-lion"},
                                  "c": {"lion", "monkey"}})


if __name__ == "__main__":
    unittest.main()
