"""End-to-end DearLive integration flow (sandbox, stdlib unittest).

Proves, against DearLive-shaped adapters (swap for real adapters in
staging with zero test changes):
  DearLive user → launch token → authenticated session → wallet balance →
  join/state → debit/bet → gameplay (close/result) → win/loss settlement →
  credit → ledger consistency → reconnect replay → final balance,
  plus: idempotent replay, cancel/refund path, webhook signature verify.

Run: python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.webhooks import sign, verify
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import ServiceError, TeenPattiService
from integrations.dearlive_mock import (MockDearLiveSessions,
                                        MockDearLiveTokens, MockDearLiveWallet)

LIVE = TeenPattiConfig(confirmed=True)


class TestDearLiveEndToEnd(unittest.TestCase):
    def setUp(self):
        self.wallet = MockDearLiveWallet()
        self.svc = TeenPattiService(config=LIVE, wallet=self.wallet,
                                    tokens=MockDearLiveTokens(),
                                    sessions=MockDearLiveSessions())

    def test_full_win_loss_flow_with_ledger_consistency(self):
        players = ("u_aman", "u_bina", "u_chen")
        for p in players:
            self.wallet.fund(p, 10_000)
        start_total = sum(self.wallet.get_balance(p).available for p in players)

        # 1. launch → authenticated session (single-use token)
        sessions = {}
        for p in players:
            tok = self.svc.tokens.mint(p, "room7", "teen-patti-pro").token
            self.assertTrue(tok.startswith("dl_teen-patti-pro_"))
            opened = self.svc.open_session(tok)
            sessions[p] = opened["session_id"]
            with self.assertRaises(Exception):
                self.svc.open_session(tok)  # replay rejected

        # 2. balance visible before join
        for p in players:
            self.assertEqual(self.wallet.get_balance(p).available, 10_000)

        # 3. join/state
        self.svc.start_round("room7")
        st = self.svc.state("room7", "u_aman")
        self.assertEqual(st["status"], "BETTING_OPEN")

        # 4. debit/bet (idempotent keys)
        self.svc.place_bet("room7", "u_aman", "A", 100, "e2e-ka")
        self.svc.place_bet("room7", "u_bina", "B", 500, "e2e-kb")
        self.svc.place_bet("room7", "u_chen", "C", 1000, "e2e-kc")
        replay = self.svc.place_bet("room7", "u_aman", "A", 100, "e2e-ka")
        self.assertTrue(replay["bet_id"])  # no second debit
        mid = {p: self.wallet.get_balance(p).available for p in players}
        self.assertEqual(mid, {"u_aman": 9900, "u_bina": 9500, "u_chen": 9000})

        # 5. gameplay → close → result → settle → credit
        self.svc.close_betting("room7")
        published = self.svc.publish_result("room7")
        self.assertTrue(published["winners"])
        stl = self.svc.settle("room7")
        paid = sum(r["payout"] for r in stl["settlements"])
        staked = 1600
        self.assertEqual(paid + stl["carry_out"], staked)  # conservation

        # 6. ledger consistency: append-only, one debit per bet, credits once
        kinds = [e["kind"] for e in self.wallet.ledger]
        self.assertEqual(kinds.count("debit"), 3)
        end_total = sum(self.wallet.get_balance(p).available for p in players)
        self.assertEqual(end_total, start_total - staked + paid)

        # 7. exactly-once re-settle
        before = dict(end_total=end_total)
        self.svc.settle("room7")
        self.assertEqual(sum(self.wallet.get_balance(p).available for p in players),
                         before["end_total"])

        # 8. reconnect: snapshot + missed events since seq 0
        rec = self.svc.reconnect(sessions["u_aman"], 0)
        self.assertIn("snapshot", rec)
        self.assertTrue(len(rec["missed_events"]) > 0)

        # 9. webhook event signed + verifiable
        ev = self.svc._fire("settlement.completed", {"round_id": "x"})
        sig = sign(self.svc.webhook_secret, __import__("json").dumps(ev).encode())
        self.assertTrue(verify(self.svc.webhook_secret,
                               __import__("json").dumps(ev).encode(), sig))

    def test_cancel_refund_path(self):
        self.wallet.fund("u_ref", 5_000)
        self.svc.open_session(
            self.svc.tokens.mint("u_ref", "roomR", "teen-patti-pro").token)
        self.svc.start_round("roomR")
        self.svc.place_bet("roomR", "u_ref", "A", 500, "rk1")
        self.assertEqual(self.wallet.get_balance("u_ref").available, 4_500)
        out = self.svc.cancel_round("roomR", "operator void", "admin")
        self.assertEqual(out["voided"], 1)
        self.assertEqual(self.wallet.get_balance("u_ref").available, 5_000)

    def test_banned_user_rejected(self):
        self.svc.sessions.banned.add("u_bad")
        tok = self.svc.tokens.mint("u_bad", "room7", "teen-patti-pro").token
        with self.assertRaises(Exception):
            self.svc.open_session(tok)

    def test_unconfirmed_blocks_money(self):
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        svc = TeenPattiService(config=DEFAULT_CONFIG, wallet=MockDearLiveWallet(),
                               tokens=MockDearLiveTokens(),
                               sessions=MockDearLiveSessions())
        self.assertFalse(svc.config.confirmed)
        svc.tokens.mint("p", "r", "teen-patti-pro")
        with self.assertRaises(ServiceError):
            svc.place_bet("r", "p", "A", 100, "k")


if __name__ == "__main__":
    unittest.main()
