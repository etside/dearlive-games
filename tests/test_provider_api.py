"""B2B provider API integration tests.

Covers the eighteen required scenarios end to end through a real HTTP server
with signed requests: authentication, session lifecycle, launch tokens, join
and matchmaking, a complete round, wallet debit/credit/rollback,
idempotency, reconnect, timeout, invalid action, insufficient balance,
unauthorized player, expired session, replayed request and concurrent wallet
requests.
"""
import json
import secrets
import sys
import threading
import time
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

API_KEY = "tp_live_test"
API_SECRET = "shared-secret-under-test"
OPERATOR = "uradhura"


def signed(method, url_path, body=None, api_key=API_KEY, secret=API_SECRET,
           timestamp=None, nonce=None, extra=None, raw_body=None):
    """Build headers for a signed provider request. Returns (headers, body).

    The canonical string signs the path without its query string.
    """
    payload = raw_body
    if payload is None:
        payload = json.dumps(body).encode() if body is not None else b""
    ts = int(time.time()) if timestamp is None else timestamp
    n = nonce or secrets.token_hex(8)
    sign_path = url_path.split("?", 1)[0]
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
        "X-Timestamp": str(ts),
        "X-Nonce": n,
        "X-Signature": PA.sign_request(secret, method, sign_path, ts, n, payload),
    }
    headers.update(extra or {})
    return headers, payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


def http(method, url, headers=None, body=b"", raw=False, follow=True):
    req = urllib.request.Request(url, data=body or None, headers=headers or {},
                                 method=method)
    opener = urllib.request.urlopen if follow else _NO_REDIRECT.open
    try:
        with opener(req, timeout=10) as resp:
            data = resp.read().decode()
            return resp.status, (data if raw else json.loads(data or "{}")), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        data = exc.read().decode()
        try:
            return exc.code, (data if raw else json.loads(data or "{}")), dict(exc.headers)
        except ValueError:
            return exc.code, data, dict(exc.headers)


class ProviderApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wallet = MemoryWallet()
        wallet.fund("player_10025", 50000)
        wallet.fund("player_10026", 500)
        cls.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                                   wallet=wallet)
        Handler.svc = cls.svc
        Handler.wheels = {}
        ctx = build_context(cls.svc, wallet, keys={API_KEY: API_SECRET},
                            base_url="", client_path="/teen-patti-pro/?session=")
        Handler.provider_ctx = ctx
        Handler.provider_tokens = ctx.tokens
        cls.ctx = ctx
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def api(self, method, path, body=None, extra=None, key=API_KEY,
            secret=API_SECRET, timestamp=None, nonce=None):
        headers, payload = signed(method, path, body, key, secret, timestamp,
                                  nonce, extra)
        return http(method, self.base + path, headers, payload)

    def make_session(self, player_id="player_10025", **body):
        request = {"player_id": player_id, "game_code": "teen_patti_pro",
                   "currency": "COIN", "language": "en", "platform": "android",
                   "return_url": "https://dearlive.example.com/games"}
        request.update(body)
        status, payload, _ = self.api("POST", "/api/v1/sessions", request)
        self.assertEqual(status, 201, payload)
        return payload["data"]

    # 1. API-key authentication
    def test_01_api_key_authentication(self):
        status, payload, _ = http("GET", self.base + "/api/v1/games")
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "UNAUTHENTICATED")
        status, payload, _ = self.api("GET", "/api/v1/games", key="tp_unknown")
        self.assertEqual(status, 401)
        headers, raw = signed("GET", "/api/v1/games")
        headers["X-Signature"] = "0" * 64
        status, payload, _ = http("GET", self.base + "/api/v1/games", headers, raw)
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "INVALID_SIGNATURE")
        status, payload, _ = self.api("GET", "/api/v1/games",
                                      timestamp=int(time.time()) - 4000)
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "INVALID_TIMESTAMP")
        status, payload, _ = self.api("GET", "/api/v1/games")
        self.assertEqual(status, 200)

    # 2. session creation
    def test_02_session_creation(self):
        session = self.make_session()
        for field in ("session_id", "session_token", "launch_url", "expires_at"):
            self.assertIn(field, session)
        self.assertTrue(session["session_token"].startswith("gst_"))
        self.assertIn(session["session_token"], session["launch_url"])
        self.assertEqual(session["game_code"], "teen_patti_pro")
        status, payload, _ = self.api("POST", "/api/v1/sessions",
                                      {"player_id": "player_10025",
                                       "currency": "EUR"})
        self.assertEqual(status, 422)
        status, payload, _ = self.api("POST", "/api/v1/sessions", {})
        self.assertEqual(status, 422)

    # 3. launch-token validation
    def test_03_launch_token_validation(self):
        session = self.make_session()
        status, payload, headers = http(
            "GET", self.base + "/api/v1/provider/launch/" + session["session_token"],
            follow=False)
        self.assertEqual(status, 302)
        self.assertIn(session["session_token"], headers["Location"])
        status, payload, _ = http(
            "GET", self.base + "/api/v1/provider/launch/gst_not_a_real_token")
        self.assertEqual(status, 401)
        token = self.ctx.tokens.mint("sess-x", {"player_id": "player_10025"},
                                     ttl_s=1)
        time.sleep(1.1)
        self.assertIsNone(self.ctx.tokens.get(token["token"]))

    # 4. player join
    def test_04_player_join(self):
        session = self.make_session("player_10025", table_id="teen-patti-low")
        status, payload, _ = self.api(
            "POST", "/api/v1/teen-patti/tables/teen-patti-low/join",
            {"session_id": session["session_id"]})
        self.assertEqual(status, 200, payload)
        self.assertIn("player_10025", payload["data"]["seats"])
        status, payload, _ = self.api(
            "POST", "/api/v1/teen-patti/tables/teen-patti-low/join",
            {"session_token": session["session_token"]})
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["already_seated"])
        status, payload, _ = self.api(
            "POST", "/api/v1/teen-patti/tables/nope/join", {"player_id": "player_10025"})
        self.assertEqual(status, 404)

    # 5. matchmaking / table selection
    def test_05_matchmaking(self):
        status, payload, _ = self.api("GET", "/api/v1/teen-patti/tables")
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["tables"])
        session = self.make_session("player_10025", amount=100)
        self.assertIn(session["table_id"],
                      [t["table_id"] for t in payload["data"]["tables"]])
        self.assertEqual(session["table_id"], "teen-patti-low")
        detail = [t for t in payload["data"]["tables"]
                  if t["table_id"] == "teen-patti-low"][0]
        self.assertLessEqual(detail["min_bet"], 100)
        self.assertLessEqual(100, detail["max_bet"])
        big = self.make_session("player_10026", amount=9000)
        self.assertEqual(big["table_id"], "teen-patti-high")

    # 6. complete round
    def test_06_complete_round(self):
        session = self.make_session("player_10025", table_id="teen-patti-mid",
                                    amount=100)
        table = session["table_id"]
        self.api("POST", f"/api/v1/teen-patti/tables/{table}/join",
                 {"session_id": session["session_id"]})
        status, payload, _ = self.api(
            "GET", f"/api/v1/teen-patti/tables/{table}/state?player_id=player_10025")
        self.assertEqual(status, 200)
        state = payload["data"]["state"]
        self.assertEqual(state["status"], "BETTING_OPEN")
        self.assertEqual(state["hands"], {"A": ["**"] * 3, "B": ["**"] * 3,
                                          "C": ["**"] * 3})
        before = self.svc.wallet.get_balance("player_10025").available
        status, payload, _ = self.api(
            "POST", f"/api/v1/teen-patti/tables/{table}/action",
            {"action": "bet", "session_id": session["session_id"],
             "position": "A", "amount": 100},
            extra={"Idempotency-Key": "round-6-bet-1"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["position"], "A")
        mid = self.svc.wallet.get_balance("player_10025").available
        self.assertEqual(mid, before - 100)
        self.svc.sweep(int(time.time() * 1000) + 10 ** 9)
        after = self.svc.wallet.get_balance("player_10025").available
        self.assertGreaterEqual(after, mid)
        status, payload, _ = self.api("GET", f"/api/v1/teen-patti/tables/{table}/history")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["data"]["count"], 1)
        round_row = payload["data"]["rounds"][0]
        self.assertIn("settlements", round_row)
        self.assertIn("winners", round_row)
        settled_ids = {row["bet_id"] for row in round_row["settlements"]}
        self.assertTrue(settled_ids)
        payouts = [row["payout"] for row in round_row["settlements"]]
        self.assertTrue(all(p >= 0 for p in payouts))
        for row in round_row["settlements"]:
            if row["payout"]:
                self.assertEqual(after, mid + row["payout"])

    # 7. wallet debit
    def test_07_wallet_debit(self):
        before = self.svc.wallet.get_balance("player_10025").available
        status, payload, _ = self.api(
            "POST", "/api/v1/wallet/debit",
            {"player_id": "player_10025", "amount": 250, "currency": "COIN",
             "game_code": "teen_patti_pro", "round_id": "r-debit",
             "reference": "r-debit-bet-1"},
            extra={"Idempotency-Key": "r-debit-bet-1"})
        self.assertEqual(status, 200, payload)
        txn = payload["data"]
        self.assertEqual(txn["type"], "debit")
        self.assertEqual(txn["amount"], 250)
        self.assertEqual(self.svc.wallet.get_balance("player_10025").available,
                         before - 250)

    # 8. wallet credit
    def test_08_wallet_credit(self):
        before = self.svc.wallet.get_balance("player_10025").available
        status, payload, _ = self.api(
            "POST", "/api/v1/wallet/credit",
            {"player_id": "player_10025", "amount": 600, "currency": "COIN",
             "game_code": "teen_patti_pro", "round_id": "r-credit",
             "reference": "r-credit-win-1"},
            extra={"Idempotency-Key": "r-credit-win-1"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["type"], "credit")
        self.assertEqual(self.svc.wallet.get_balance("player_10025").available,
                         before + 600)

    # 9. rollback
    def test_09_rollback(self):
        self.api("POST", "/api/v1/wallet/debit",
                 {"player_id": "player_10025", "amount": 100,
                  "reference": "r-rollback-bet-1"},
                 extra={"Idempotency-Key": "r-rollback-bet-1"})
        after_debit = self.svc.wallet.get_balance("player_10025").available
        status, payload, _ = self.api(
            "POST", "/api/v1/wallet/rollback",
            {"player_id": "player_10025",
             "original_reference": "r-rollback-bet-1",
             "reason": "ROUND_CANCELLED"},
            extra={"Idempotency-Key": "r-rollback-1"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["type"], "rollback")
        self.assertEqual(self.svc.wallet.get_balance("player_10025").available,
                         after_debit + 100)
        status, payload, _ = self.api(
            "POST", "/api/v1/wallet/rollback",
            {"player_id": "player_10025",
             "original_reference": "r-rollback-bet-1",
             "reason": "ROUND_CANCELLED"},
            extra={"Idempotency-Key": "r-rollback-2"})
        self.assertEqual(status, 409)
        self.assertEqual(self.svc.wallet.get_balance("player_10025").available,
                         after_debit + 100)

    # 10. duplicate transaction / idempotency
    def test_10_idempotency(self):
        body = {"player_id": "player_10025", "amount": 300,
                "reference": "r-idem-1"}
        status, first, _ = self.api("POST", "/api/v1/wallet/debit", body,
                                    extra={"Idempotency-Key": "r-idem-1"})
        self.assertEqual(status, 200)
        balance = self.svc.wallet.get_balance("player_10025").available
        for _ in range(3):
            status, again, _ = self.api("POST", "/api/v1/wallet/debit", body,
                                        extra={"Idempotency-Key": "r-idem-1"})
            self.assertEqual(status, 200)
            self.assertEqual(again["data"]["txn_id"], first["data"]["txn_id"])
            self.assertTrue(again["data"]["replayed"])
        self.assertEqual(self.svc.wallet.get_balance("player_10025").available,
                         balance)
        status, payload, _ = self.api(
            "POST", "/api/v1/wallet/debit",
            {"player_id": "player_10025", "amount": 999, "reference": "r-idem-1"},
            extra={"Idempotency-Key": "r-idem-1"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "DUPLICATE_REQUEST")
        status, payload, _ = self.api("POST", "/api/v1/wallet/debit", body)
        self.assertEqual(status, 422)

    # 11. reconnect
    def test_11_reconnect(self):
        session = self.make_session("player_10025", table_id="teen-patti-high")
        snapshot = self.svc.state(session["table_id"], "player_10025")
        result = self.svc.reconnect(session["session_id"], 0)
        self.assertEqual(result["session_id"], session["session_id"])
        self.assertEqual(result["snapshot"]["room_id"], session["table_id"])
        self.assertEqual(result["snapshot"].get("round_id", ""),
                         snapshot.get("round_id", ""))
        status, payload, _ = self.api(
            "GET", f"/api/v1/sessions/{session['session_id']}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["active"])

    # 12. timeout
    def test_12_timeout_settles_round(self):
        session = self.make_session("player_10025", table_id="teen-patti-low",
                                    amount=100)
        self.api("POST", "/api/v1/teen-patti/tables/teen-patti-low/join",
                 {"session_id": session["session_id"]})
        self.svc.ensure_round("teen-patti-low")
        self.api("POST", "/api/v1/teen-patti/tables/teen-patti-low/action",
                 {"action": "bet", "session_id": session["session_id"],
                  "position": "B", "amount": 100},
                 extra={"Idempotency-Key": "timeout-bet-1"})
        round_id = self.svc.state("teen-patti-low", "player_10025")["round_id"]
        reports = self.svc.sweep(int(time.time() * 1000) + 10 ** 9)
        self.assertTrue(any(r.get("actions") == ["closed", "result", "settled"]
                            for r in reports))
        state = self.svc.state("teen-patti-low", "player_10025")
        self.assertEqual(state["round_id"], round_id)
        self.assertIn(state["status"], ("SETTLED", "CLOSED"))

    # 13. invalid action
    def test_13_invalid_action(self):
        session = self.make_session("player_10025", table_id="teen-patti-low")
        self.svc.ensure_round("teen-patti-low")
        for action in ("fold", "show", "hack", ""):
            status, payload, _ = self.api(
                "POST", "/api/v1/teen-patti/tables/teen-patti-low/action",
                {"action": action, "session_id": session["session_id"],
                 "position": "A", "amount": 20},
                extra={"Idempotency-Key": f"invalid-{action or 'empty'}"})
            self.assertEqual(status, 422, (action, payload))
        status, payload, _ = self.api(
            "POST", "/api/v1/teen-patti/tables/teen-patti-low/action",
            {"action": "bet", "session_id": session["session_id"],
             "position": "Z", "amount": 20},
            extra={"Idempotency-Key": "invalid-position"})
        self.assertEqual(status, 422)
        status, payload, _ = self.api(
            "POST", "/api/v1/teen-patti/tables/teen-patti-low/action",
            {"action": "bet", "session_id": session["session_id"],
             "position": "A", "amount": 7},
            extra={"Idempotency-Key": "invalid-amount"})
        self.assertEqual(status, 422)

    # 14. insufficient balance
    def test_14_insufficient_balance(self):
        before = self.svc.wallet.get_balance("player_10026").available
        status, payload, _ = self.api(
            "POST", "/api/v1/wallet/debit",
            {"player_id": "player_10026", "amount": before + 10_000,
             "reference": "r-insufficient-1"},
            extra={"Idempotency-Key": "r-insufficient-1"})
        self.assertEqual(status, 402, payload)
        self.assertEqual(payload["code"], "INSUFFICIENT_BALANCE")
        self.assertEqual(self.svc.wallet.get_balance("player_10026").available, before)

    # 15. unauthorized player
    def test_15_unauthorized_player(self):
        session = self.make_session("player_10025", table_id="teen-patti-low")
        self.api("POST", "/api/v1/teen-patti/tables/teen-patti-low/join",
                 {"session_id": session["session_id"]})
        self.svc.ensure_round("teen-patti-low")
        status, payload, _ = self.api(
            "POST", "/api/v1/teen-patti/tables/teen-patti-low/action",
            {"action": "bet", "session_token": "gst_forged_token",
             "position": "A", "amount": 20},
            extra={"Idempotency-Key": "forged-session"})
        self.assertEqual(status, 401)
        status, payload, _ = self.api(
            "GET", "/api/v1/teen-patti/tables/teen-patti-low/state",
            extra={"Authorization": "Bearer gst_forged_token"})
        self.assertEqual(status, 401)
        status, payload, _ = self.api(
            "POST", "/api/v1/wallet/rollback",
            {"player_id": "player_10026", "original_reference": "r-debit-bet-1",
             "reason": "NOPE"},
            extra={"Idempotency-Key": "rollback-wrong-player"})
        self.assertEqual(status, 403)

    # 16. expired session
    def test_16_expired_session(self):
        token = self.ctx.tokens.mint("sess-expired", {"player_id": "player_10025"},
                                     ttl_s=1)
        time.sleep(1.1)
        status, payload, _ = http(
            "GET", self.base + "/api/v1/provider/launch/" + token["token"],
            follow=False)
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "INVALID_SESSION_TOKEN")
        status, payload, _ = self.api("GET", "/api/v1/sessions/sess-nope")
        self.assertEqual(status, 401)
        live = self.make_session("player_10025")
        status, payload, _ = self.api("DELETE",
                                      f"/api/v1/sessions/{live['session_id']}")
        self.assertEqual(status, 200)
        status, payload, _ = self.api("GET", f"/api/v1/sessions/{live['session_id']}")
        self.assertEqual(status, 401)

    # 17. replayed request
    def test_17_replayed_request(self):
        headers, payload = signed("GET", "/api/v1/games")
        status, first, _ = http("GET", self.base + "/api/v1/games", headers, payload)
        self.assertEqual(status, 200)
        status, second, _ = http("GET", self.base + "/api/v1/games", headers, payload)
        self.assertEqual(status, 401)
        self.assertEqual(second["code"], "REPLAYED_REQUEST")

    # 18. concurrent wallet requests
    def test_18_concurrent_wallet_requests(self):
        player = "player_10025"
        before = self.svc.wallet.get_balance(player).available
        self.svc.wallet.fund(player, 10_000)
        funded = self.svc.wallet.get_balance(player).available
        key = "r-concurrent-1"
        results = []
        lock = threading.Lock()

        def worker(index):
            status, payload, _ = self.api(
                "POST", "/api/v1/wallet/debit",
                {"player_id": player, "amount": 100, "reference": key},
                extra={"Idempotency-Key": key})
            with lock:
                results.append((status, payload))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 8)
        txn_ids = {payload["data"]["txn_id"] for status, payload in results
                   if status == 200}
        self.assertEqual(len(txn_ids), 1)
        self.assertEqual(self.svc.wallet.get_balance(player).available, funded - 100)
        distinct = [f"r-concurrent-b{i}" for i in range(8)]

        def distinct_worker(reference):
            status, payload, _ = self.api(
                "POST", "/api/v1/wallet/debit",
                {"player_id": player, "amount": 10, "reference": reference},
                extra={"Idempotency-Key": reference})
            with lock:
                results.append((status, payload))

        results.clear()
        threads = [threading.Thread(target=distinct_worker, args=(ref,))
                   for ref in distinct]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        ok = [p for s, p in results if s == 200]
        self.assertEqual(len(ok), 8)
        self.assertEqual(len({p["data"]["txn_id"] for p in ok}), 8)
        self.assertEqual(self.svc.wallet.get_balance(player).available, funded - 180)
        self.assertGreater(self.svc.wallet.get_balance(player).available, before)


class ProviderContractTest(unittest.TestCase):
    """Contract-level checks that do not need a running server."""

    def setUp(self):
        self.wallet = MemoryWallet()
        self.wallet.fund("player_10025", 1000)
        self.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                                    wallet=self.wallet)
        self.ctx = build_context(self.svc, self.wallet,
                                 keys={API_KEY: API_SECRET}, base_url="http://host")

    def call(self, method, path, body=None, extra=None, **kwargs):
        headers, payload = signed(method, path, body, **kwargs)
        headers.update(extra or {})
        from provider.router import dispatch
        return dispatch(self.ctx, method, path, "", headers, payload)

    def test_rate_limit_returns_429(self):
        self.ctx.limiter = type(self.ctx.limiter)(limit=3, window_s=60)
        statuses = [self.call("GET", "/api/v1/games")[0] for _ in range(5)]
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3], 429)
        self.assertEqual(statuses[4], 429)

    def test_method_not_allowed_and_unknown_route(self):
        status, _, payload = self.call("DELETE", "/api/v1/games")
        self.assertEqual(status, 405)
        self.assertIn("Allow", _)
        status, _, payload = self.call("GET", "/api/v1/teen-patti/nope")
        self.assertEqual(status, 404)

    def test_body_too_large_rejected(self):
        big = b'{"player_id":"' + b"a" * (70 * 1024) + b'"}'
        status, _, payload = self.call("POST", "/api/v1/sessions", raw_body=big)
        self.assertEqual(status, 413)

    def test_ledger_is_append_only(self):
        self.call("POST", "/api/v1/wallet/debit",
                  {"player_id": "player_10025", "amount": 100,
                   "reference": "ledger-1"},
                  extra={"Idempotency-Key": "ledger-1"})
        self.call("POST", "/api/v1/wallet/rollback",
                  {"player_id": "player_10025",
                   "original_reference": "ledger-1", "reason": "CANCEL"},
                  extra={"Idempotency-Key": "ledger-rb-1"})
        rows = self.ctx.wallet.transactions("player_10025", 50)
        self.assertEqual([r["type"] for r in rows], ["rollback", "debit"])
        original = [r for r in rows if r["type"] == "debit"][0]
        self.assertEqual(original["status"], "SUCCESS")
        self.assertEqual(original["amount"], 100)
        self.assertEqual(self.wallet.get_balance("player_10025").available, 1000)

    def test_health_is_public_and_leaks_no_secret(self):
        from provider.router import dispatch
        status, _, payload = dispatch(self.ctx, "GET", "/api/v1/provider/health",
                                      "", {}, b"")
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["provider_auth_configured"])
        self.assertNotIn(API_SECRET, json.dumps(payload))

    def test_openapi_and_docs_served(self):
        from provider.router import dispatch
        status, headers, raw = dispatch(self.ctx, "GET", "/openapi.json", "", {}, b"")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        spec = json.loads(raw)
        self.assertTrue(spec["openapi"].startswith("3.1"))
        self.assertIn("/api/v1/sessions", spec["paths"])
        self.assertIn("/api/v1/wallet/rollback", spec["paths"])
        status, headers, page = dispatch(self.ctx, "GET", "/docs", "", {}, b"")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("ProviderHmac", page)

    def test_generated_artifacts_match_spec(self):
        from provider.spec import SPEC
        from provider.yamlgen import to_yaml
        yaml_path = Path(__file__).resolve().parents[1] / "docs" / "openapi.yaml"
        postman_path = Path(__file__).resolve().parents[1] / "docs" / "postman_collection.json"
        self.assertIn('"openapi": "3.1.0"', to_yaml(SPEC))
        self.assertIn('"openapi": "3.1.0"', yaml_path.read_text(encoding="utf-8"))
        collection = json.loads(postman_path.read_text(encoding="utf-8"))
        self.assertTrue(collection["item"])
        self.assertTrue(collection.get("event"))

    def test_no_secret_in_launch_url_or_session_payload(self):
        status, _, payload = self.call(
            "POST", "/api/v1/sessions",
            {"player_id": "player_10025", "game_code": "teen_patti_pro"})
        self.assertEqual(status, 201)
        body = json.dumps(payload)
        self.assertNotIn(API_SECRET, body)


if __name__ == "__main__":
    unittest.main()
