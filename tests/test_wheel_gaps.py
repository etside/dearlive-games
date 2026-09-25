"""Regression cover for the documented wheel-engine gaps.

Each test corresponds to a gap recorded in docs/GAME_RULES_WHEELS.md section 9.
These are money-path and correctness guarantees, so they are pinned explicitly
rather than left to the rules document.
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet, WalletError
from games.wheel_common.configs import greedy_config
from games.wheel_common.service import ServiceError, WheelService
from provider import auth as PA
from provider.context import build_context
from provider.router import dispatch

GAME = "monkey_wheel"
SLUG = "greedy-monkey"
KEY = "tp_gaps"
SECRET = "gaps-secret"


def service(confirmed=True, fund=20000):
    cfg = greedy_config()
    cfg.confirmed = confirmed
    wallet = MemoryWallet()
    wallet.fund("p1", fund)
    return WheelService(config=cfg, wallet=wallet), wallet


class SettlementDurabilityTest(unittest.TestCase):
    """A round must not be marked settled before the money actually moves."""

    def test_credit_failure_leaves_round_retryable(self):
        svc, wallet = service()
        room = "dur-1"
        svc.start_round(room)
        # Bet every option so at least one wins and a payout credit is required;
        # the outcome is RNG-driven, so this makes the test deterministic.
        for index, option in enumerate(svc._options(svc._room(room))):
            svc.place_bet(room, "p1", option.option_id, 20, f"k-{index}")

        original_credit = wallet.credit
        calls = {"n": 0}

        def flaky_credit(player_id, amount, ref, idempotency_key):
            calls["n"] += 1
            raise WalletError("upstream wallet unavailable")

        wallet.credit = flaky_credit
        svc.close_betting(room)
        svc.publish_result(room)
        with self.assertRaises(WalletError):
            svc.settle(room)

        round_obj = svc._room(room).round
        self.assertFalse(round_obj._settled,
                         "round must stay un-settled so settlement can be retried")
        self.assertNotEqual(round_obj.status.value, "CLOSED")

        wallet.credit = original_credit
        result = svc.settle(room)
        self.assertTrue(svc._room(room).round._settled)
        self.assertTrue(result["settlements"])
        payouts = [row["payout"] for row in result["settlements"]]
        # Every option was staked, so the net may be below the opening balance;
        # what matters is that the winning option was actually paid.
        self.assertTrue(any(p > 0 for p in payouts), payouts)

    def test_settlement_is_not_paid_twice_on_replay(self):
        svc, wallet = service()
        room = "dur-2"
        svc.start_round(room)
        svc.place_bet(room, "p1", "banana", 20, "k1")
        svc.close_betting(room)
        svc.publish_result(room)
        first = svc.settle(room)
        balance = wallet.get_balance("p1").available
        svc.settle(room)
        svc.settle(room)
        self.assertEqual(wallet.get_balance("p1").available, balance)
        self.assertEqual(first["settlements"], svc._room(room).round.settlements)


class ConfirmedGateTest(unittest.TestCase):
    """Every money-moving path must respect the unconfirmed-rules gate."""

    def test_cancel_round_is_blocked_while_unconfirmed(self):
        svc, _ = service(confirmed=False)
        room = "gate-1"
        with self.assertRaises(ServiceError) as caught:
            svc.cancel_round(room, "test", "admin")
        self.assertIn("TBC", str(caught.exception).upper())

    def test_set_autobet_is_blocked_while_unconfirmed(self):
        svc, _ = service(confirmed=False)
        with self.assertRaises(ServiceError) as caught:
            svc.set_autobet("gate-2", "p1", "banana", 20, 3)
        self.assertIn("TBC", str(caught.exception).upper())

    def test_set_autobet_allowed_once_confirmed(self):
        cfg = greedy_config()
        cfg.confirmed = True
        cfg.auto_allowed = True
        svc = WheelService(config=cfg, wallet=MemoryWallet())
        saved = svc.set_autobet("gate-3", "p1", "banana", 20, 3)
        self.assertEqual(saved["rounds_left"], 3)

    def test_place_bet_still_blocked_while_unconfirmed(self):
        svc, _ = service(confirmed=False)
        svc.start_round("gate-4")
        with self.assertRaises(ServiceError):
            svc.place_bet("gate-4", "p1", "banana", 20, "k1")


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.wallet = service()

    def test_non_integer_amount_is_rejected(self):
        self.svc.start_round("v-1")
        for bad in ("20", 20.5, True, None):
            with self.subTest(amount=bad):
                with self.assertRaises(ServiceError) as caught:
                    self.svc.place_bet("v-1", "p1", "banana", bad, f"k-{bad}")
                self.assertIn("integer", str(caught.exception).lower() + " "
                              if "integer" in str(caught.exception).lower()
                              else "amount")

    def test_zero_and_negative_amount_rejected(self):
        self.svc.start_round("v-2")
        for bad in (0, -20):
            with self.subTest(amount=bad):
                with self.assertRaises(ServiceError):
                    self.svc.place_bet("v-2", "p1", "banana", bad, f"k-neg-{bad}")

    def test_no_active_options_refuses_result_cleanly(self):
        cfg = greedy_config()
        cfg.confirmed = True
        svc = WheelService(config=cfg, wallet=MemoryWallet())
        room = "v-3"
        svc.start_round(room)
        for option in svc._options(svc._room(room)):
            option.is_active = False
        svc.close_betting(room)
        with self.assertRaises(ServiceError) as caught:
            svc.publish_result(room)
        self.assertIn("No active betting options", str(caught.exception))
        self.assertNotIsInstance(caught.exception, ValueError)

    def test_skipped_autobet_is_audited(self):
        cfg = greedy_config()
        cfg.confirmed = True
        cfg.auto_allowed = True
        wallet = MemoryWallet()  # deliberately unfunded
        svc = WheelService(config=cfg, wallet=wallet)
        room = "v-4"
        svc.set_autobet(room, "p1", "banana", 20, 2)
        svc.start_round(room)
        actions = [entry["action"] for entry in svc.audit.entries]
        self.assertIn("autobet.skipped", actions)


class ProviderTableLimitTest(unittest.TestCase):
    """The provider promises table limits; the action route must enforce them."""

    def setUp(self):
        cfg = greedy_config()
        cfg.confirmed = True
        wheel = WheelService(config=cfg, wallet=MemoryWallet())
        wheel.wallet.fund("p1", 50000)
        from games.teen_patti_pro.config import TeenPattiConfig
        from games.teen_patti_pro.service import TeenPattiService
        teen = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                                wallet=MemoryWallet())
        self.ctx = build_context(teen, teen.wallet, keys={KEY: SECRET})
        self.ctx.attach_games(teen, {"greedy-monkey": wheel})
        self.wheel = wheel

    def call(self, method, path, body=None, extra=None, query=""):
        payload = json.dumps(body).encode() if body is not None else b""
        ts = int(time.time())
        nonce = os.urandom(8).hex()
        headers = {"X-API-Key": KEY, "X-Timestamp": str(ts), "X-Nonce": nonce,
                   "X-Signature": PA.sign_request(SECRET, method, path, ts, nonce,
                                                  payload)}
        headers.update(extra or {})
        return dispatch(self.ctx, method, path, query, headers, payload)

    def test_amount_above_table_maximum_is_refused(self):
        status, _h, payload = self.call(
            "POST", "/api/v1/sessions",
            {"player_id": "p1", "game_code": GAME, "currency": "COIN",
             "table_id": "greedy-monkey-low"})
        self.assertEqual(status, 201, payload)
        session = payload["data"]
        status, _h, payload = self.call(
            "POST", f"/api/v1/{SLUG}/tables/greedy-monkey-low/action",
            {"action": "bet", "session_id": session["session_id"],
             "option_id": "banana", "amount": 100000},
            {"Idempotency-Key": "limit-1"})
        self.assertEqual(status, 422, payload)
        self.assertIn("between", payload["message"])

    def test_amount_inside_table_limits_is_accepted(self):
        status, _h, payload = self.call(
            "POST", "/api/v1/sessions",
            {"player_id": "p1", "game_code": GAME, "currency": "COIN",
             "table_id": "greedy-monkey-low"})
        session = payload["data"]
        status, _h, payload = self.call(
            "POST", f"/api/v1/{SLUG}/tables/greedy-monkey-low/action",
            {"action": "bet", "session_id": session["session_id"],
             "option_id": "banana", "amount": 100},
            {"Idempotency-Key": "limit-2"})
        self.assertEqual(status, 200, payload)

    def test_history_exposes_earnings_for_identified_player(self):
        status, _h, payload = self.call(
            "POST", "/api/v1/sessions",
            {"player_id": "p1", "game_code": GAME, "currency": "COIN"})
        self.assertEqual(status, 201, payload)
        table = payload["data"]["table_id"]
        status, _h, payload = self.call(
            "GET", f"/api/v1/{SLUG}/tables/{table}/history",
            query="player_id=p1")
        self.assertEqual(status, 200, payload)
        self.assertIn("earnings_today", payload["data"])


if __name__ == "__main__":
    unittest.main()
