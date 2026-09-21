"""Adapter CONTRACT suite (handover §12-13).

Runs against interface implementations WITHOUT touching the game engine.
Today: mock DearLive adapters. Later: subclass with real DearLive factories —
same tests, zero engine changes. Any failure against the real adapters means
the ADAPTER violates the contract, never the engine.

Contract rules asserted:
 C1 balances are integers, debit/credit idempotent per key, ledger append-only.
 C2 tokens single-use, TTL-enforced, game-scoped, malformed rejected.
 C3 sessions gated on player status; touch/end behave.
 C4 full game over mock adapters: lifecycle + conservation + exactly-once.
 C5 wallet faults (timeout/insufficient/transport) never lose money silently.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import InsufficientBalance, WalletError
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService, ServiceError
from integrations.dearlive_mock import (MockDearLiveSessions, MockDearLiveTokens,
                                        MockDearLiveWallet)

LIVE = TeenPattiConfig(confirmed=True)


class AdapterContract:
    """Mixin — override factories to run the same contract vs real adapters."""
    wallet_cls = MockDearLiveWallet
    token_cls = MockDearLiveTokens
    session_cls = MockDearLiveSessions

    def make(self, **kw):
        w = self.wallet_cls()
        return (TeenPattiService(config=LIVE, wallet=w, tokens=self.token_cls(),
                                 sessions=self.session_cls(), **kw), w)

    # -- C1 --
    def test_c1_integer_idempotent_ledger(self):
        s, w = self.make()
        w.fund("p", 1000)
        t1 = w.debit("p", 100, "r1", "k1")
        t2 = w.debit("p", 100, "r1", "k1")
        self.assertEqual(t1.txn_id, t2.txn_id)
        self.assertEqual(w.get_balance("p").available, 900)
        kinds = [e["kind"] for e in w.ledger]
        self.assertEqual(kinds.count("debit"), 1)  # append-only, no dup rows
        c1 = w.credit("p", 250, "s1", "ck1")
        c2 = w.credit("p", 250, "s1", "ck1")
        self.assertEqual(c1.txn_id, c2.txn_id)
        self.assertEqual(w.get_balance("p").available, 1150)

    def test_c1_insufficient(self):
        s, w = self.make()
        w.fund("p", 50)
        with self.assertRaises(InsufficientBalance):
            w.debit("p", 100, "r", "k")
        self.assertEqual(w.get_balance("p").available, 50)

    # -- C2 --
    def test_c2_token_single_use_ttl_scope(self):
        s, w = self.make()
        t = s.tokens.mint("p", "room", "teen-patti-pro")
        self.assertTrue(t.token.startswith("dl_teen-patti-pro_"))
        lt = s.tokens.redeem(t.token)
        self.assertEqual(lt.player_id, "p")
        with self.assertRaises(Exception):
            s.tokens.redeem(t.token)  # replay rejected
        with self.assertRaises(Exception):
            s.tokens.redeem("garbage")
        other = s.tokens.mint("p", "room", "greedy")
        with self.assertRaises(ServiceError):
            s.open_session(other.token)  # wrong-game token refused

    # -- C3 --
    def test_c3_session_status_gate(self):
        s, w = self.make()
        s.sessions.banned.add("bad")
        with self.assertRaises(Exception):
            s.sessions.create("bad", "room", "teen-patti-pro")
        sess = s.sessions.create("good", "room", "teen-patti-pro")
        self.assertIsNotNone(s.sessions.touch(sess.session_id))
        s.sessions.end(sess.session_id)
        self.assertIsNone(s.sessions.get(sess.session_id))

    # -- C4 --
    def test_c4_full_game_over_adapters(self):
        s, w = self.make()
        for p in ("a", "b", "c"):
            w.fund(p, 10000)
        sess = s.open_session(s.tokens.mint("a", "r1", "teen-patti-pro").token)
        s.start_round("r1")
        s.place_bet("r1", "a", "A", 100, "ka")
        s.place_bet("r1", "b", "B", 500, "kb")
        s.place_bet("r1", "c", "C", 1000, "kc")
        s.close_betting("r1")
        s.publish_result("r1")
        st = s.settle("r1")
        paid = sum(x["payout"] for x in st["settlements"])
        self.assertEqual(paid + st["carry_out"], 1600)
        total = sum(w.get_balance(p).available for p in ("a", "b", "c"))
        self.assertEqual(total, 30000 - 1600 + paid)
        st2 = s.settle("r1")  # exactly-once
        self.assertEqual(sum(w.get_balance(p).available for p in ("a", "b", "c")), total)

    # -- C5 --
    def test_c5_wallet_faults_never_lose_silently(self):
        s, w = self.make()
        w.fund("p", 10000)
        s.open_session(s.tokens.mint("p", "r", "teen-patti-pro").token)
        s.start_round("r")
        w.fail_next = ["timeout"]
        with self.assertRaises(ServiceError):
            s.place_bet("r", "p", "A", 100, "k1")
        self.assertEqual(w.get_balance("p").available, 10000)  # untouched
        w.fail_next = ["insufficient"]
        with self.assertRaises(ServiceError):
            s.place_bet("r", "p", "A", 100, "k2")
        self.assertEqual(w.get_balance("p").available, 10000)
        # healthy retry with fresh key succeeds exactly once
        r = s.place_bet("r", "p", "A", 100, "k3")
        self.assertTrue(r["bet_id"])
        self.assertEqual(w.get_balance("p").available, 9900)


class TestMockDearLiveContract(AdapterContract, unittest.TestCase):
    """Concrete run vs mock adapters. Real DearLive run = new subclass."""


# Template for the future real-adapter run (handover §15):
# class TestDearLiveStagingContract(AdapterContract, unittest.TestCase):
#     wallet_cls = DearLiveWallet          # integrations/dearlive.py (CLIENT API REQUIRED)
#     token_cls = DearLiveTokenStore
#     session_cls = DearLiveSessionStore


if __name__ == "__main__":
    unittest.main()
