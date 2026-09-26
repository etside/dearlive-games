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
from provider import auth as PA
from provider.context import build_context
from provider.games import (BINDINGS, TEEN_CODE, binding_for_code,
                            binding_for_slug, canonical_code)
from provider.router import dispatch, is_provider_path

API_KEY = "tp_multi"
API_SECRET = "multi-game-secret"
# One shipped game. The retired wheel codes must resolve to None, which is
# asserted explicitly rather than left implicit.
GAMES = [(TEEN_CODE, "teen-patti-pro", "position")]
RETIRED = ("monkey_wheel", "greedy-monkey", "baby_king", "baby-king",
           "babyking", "greedy_lion")
DENOMS = (20, 100, 500, 1000)


def valid_amount(min_bet, max_bet):
    """Smallest configured denomination inside the table's own limits.

    The provider now re-checks table limits on every action, so a test must
    place a stake the chosen table actually accepts.
    """
    for value in DENOMS:
        if min_bet <= value <= max_bet:
            return value
    raise AssertionError(f"no denomination fits {min_bet}-{max_bet}")


def make_context():
    teen = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                            wallet=MemoryWallet())
    ctx = build_context(teen, teen.wallet, keys={API_KEY: API_SECRET})
    ctx.attach_games(teen)
    teen.wallet.fund("player_10025", 50000)
    return ctx, teen, {}


class GameRegistryTest(unittest.TestCase):
    def test_aliases_resolve_to_canonical_codes(self):
        self.assertEqual(canonical_code("teen-patti-pro"), TEEN_CODE)
        self.assertEqual(canonical_code("TEEN_PATTI_PRO"), TEEN_CODE)
        self.assertIsNone(canonical_code("ludo"))

    def test_retired_game_codes_no_longer_resolve(self):
        # Greedy Monkey and Baby King were removed from the bindings. Their
        # codes must resolve to None so the router answers a clear 404 rather
        # than dispatching to an engine that no longer exists.
        for raw in RETIRED:
            self.assertIsNone(canonical_code(raw), raw)
            self.assertIsNone(binding_for_code(raw), raw)
            self.assertIsNone(binding_for_slug(raw), raw)

    def test_slugs_are_unique_and_resolvable(self):
        slugs = [b.slug for b in BINDINGS.values()]
        self.assertEqual(len(slugs), len(set(slugs)))
        for code, slug, _ in GAMES:
            self.assertEqual(binding_for_slug(slug).game_code, code)
        self.assertIsNone(binding_for_slug("poker"))



    def test_unknown_game_path_is_not_a_provider_path(self):
        self.assertFalse(is_provider_path("/api/v1/poker/tables"))


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
                detail = self.data(self.call(
                    "GET", f"/api/v1/{slug}/tables/{table}"))
                stake = valid_amount(detail["min_bet"], detail["max_bet"])

                result = self.data(self.call(
                    "POST", f"/api/v1/{slug}/tables/{table}/action",
                    {"action": "bet", "session_id": session["session_id"],
                     field: choices["choices"][0]["choice"], "amount": stake},
                    {"Idempotency-Key": f"{code}-bet-1"}))
                self.assertTrue(result["accepted"])
                self.assertEqual(result[field], choices["choices"][0]["choice"])

                state = self.data(self.call(
                    "GET", f"/api/v1/{slug}/tables/{table}/state",
                    query="player_id=player_10025"))
                self.assertEqual(state["state"]["status"], "BETTING_OPEN")

                service = (self.teen if code == TEEN_CODE else
                             self.wheels["greedy-monkey" if code == MONKEY_CODE
                                         else "baby-king"])
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
                detail = self.data(self.call(
                    "GET", f"/api/v1/{slug}/tables/{table}"))
                stake = valid_amount(detail["min_bet"], detail["max_bet"])
                body = {"action": "bet", "session_id": session["session_id"],
                        field: choices["choices"][0]["choice"], "amount": stake}
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



    def test_launch_redirect_targets_the_right_game(self):
        # The API slug and the frontend route differ by design (teen-patti is
        # served at /teen-patti-pro/), so assert the real client path per game.
        client_paths = {TEEN_CODE: "/teen-patti-pro/"}
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
        Handler.svc = cls.teen
        Handler.provider_ctx = None
        Handler.provider_tokens = None
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        import games.teen_patti_pro.api as api_module
        cls.api = api_module
        cls._orig_keys = api_module.ADMIN_KEYS
        cls._orig_scopes = api_module.ADMIN_SCOPES
        api_module.ADMIN_KEYS = {"teen-op": "operator", "all-op": "operator",
                                 "wrong-op": "operator",
                                 "ro": "auditor", "super": "admin"}
        # "wrong-op" is scoped to a retired game name. With one shipped game
        # there is no second live game to be denied on, so cross-game scope
        # enforcement is exercised by scoping a key to something this
        # deployment does not serve and requiring it to be refused.
        api_module.ADMIN_SCOPES = {"teen-op": {"teen-patti-pro"},
                                   "wrong-op": {"greedy-monkey"}}
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

    def request(self, method, path, key="", bearer="", body=None, query=""):
        url = f"http://127.0.0.1:{self.port}{path}{query}"
        data = json.dumps(body or {}).encode() if method in ("POST", "PUT") else None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-Admin-Key"] = key
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        request = urllib.request.Request(url, data=data, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def post(self, path, key):
        return self.request("POST", path, key=key, body={})

    def test_whoami_reports_role_without_revealing_key(self):
        status, body = self.request("GET", "/api/v1/admin/whoami", key="teen-op")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["role"], "operator")
        self.assertEqual(body["data"]["games"], ["teen-patti-pro"])
        self.assertNotIn("teen-op", json.dumps(body))
        status, body = self.request("GET", "/api/v1/admin/whoami")
        self.assertEqual(status, 403, body)

    def test_inventory_only_lists_assigned_games(self):
        status, body = self.request("GET", "/api/v1/admin/games", key="teen-op")
        self.assertEqual(status, 200, body)
        games = [row["game_id"] for row in body["data"]]
        self.assertEqual(games, ["teen-patti-pro"])
        status, body = self.request("GET", "/api/v1/admin/games", key="all-op")
        self.assertEqual(status, 200, body)
        from provider.games import TEEN_CODE, canonical_code
        self.assertEqual({canonical_code(row["game_id"]) for row in body["data"]},
                         {TEEN_CODE})


    def test_scoped_key_allowed_on_its_game(self):
        status, body = self.post(
            "/api/v1/games/teen-patti-pro/rooms/scope-room-a/rounds/start",
            "teen-op")
        self.assertEqual(status, 200, body)

    def test_key_scoped_to_another_game_is_refused(self):
        status, body = self.post(
            "/api/v1/games/teen-patti-pro/rooms/scope-room-a2/rounds/start",
            "wrong-op")
        self.assertEqual(status, 403, body)
        self.assertIn("not scoped", body["message"])

    def test_unscoped_key_is_not_narrowed_by_the_scoped_one(self):
        status, body = self.post(
            "/api/v1/games/teen-patti-pro/rooms/scope-room-b/rounds/start",
            "all-op")
        self.assertEqual(status, 200, body)

    def test_scoped_key_still_needs_the_right_role(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}"
            "/api/v1/games/teen-patti-pro/rooms/scope-room-c/rounds/start",
            data=b"{}", method="POST",
            headers={"X-Admin-Key": "ro", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 403)


    def test_scope_parser_ignores_malformed_entries(self):
        from games.teen_patti_pro.api import _load_admin_scopes
        parsed = _load_admin_scopes("a:greedy-monkey,b;,c:monkey|wheel,:x")
        self.assertEqual(parsed, {"a": {"greedy-monkey"},
                                  "c": {"monkey", "wheel"}})


if __name__ == "__main__":
    unittest.main()
