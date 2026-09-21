"""Service tests: TBC gate, wallet atomicity, idempotency, cancel, reconnect."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService, ServiceError

TBC = TeenPattiConfig(confirmed=False)
LIVE = TeenPattiConfig(confirmed=True)


def funded(*players, amount=10000):
    w = MemoryWallet()
    for p in players:
        w.fund(p, amount)
    return w


def live_svc(w):
    s = TeenPattiService(config=LIVE, wallet=w)
    tok = s.tokens.mint("p1", "room1", "teen-patti-pro")
    sess = s.open_session(tok.token)
    s.start_round("room1")
    return s, sess


class TestGate(unittest.TestCase):
    def test_bet_blocked_unconfirmed(self):
        s = TeenPattiService(config=TBC, wallet=funded("p1"))
        with self.assertRaises(ServiceError) as cm:
            s.place_bet("room1", "p1", "A", 100, "k1")
        self.assertEqual(cm.exception.code, "TBC_RULE_UNCONFIRMED")

    def test_settle_blocked_unconfirmed(self):
        s = TeenPattiService(config=TBC, wallet=funded("p1"))
        with self.assertRaises(ServiceError) as cm:
            s.settle("room1")
        self.assertEqual(cm.exception.code, "TBC_RULE_UNCONFIRMED")


class TestMoney(unittest.TestCase):
    def test_debit_credit_flow(self):
        w = funded("p1", "p2")
        s, _ = live_svc(w)
        s.place_bet("room1", "p1", "A", 100, "k1")
        self.assertEqual(w.get_balance("p1").available, 9900)
        s.place_bet("room1", "p2", "B", 500, "k2")
        s.close_betting("room1")
        s.publish_result("room1")
        before_total = w.get_balance("p1").available + w.get_balance("p2").available
        st = s.settle("room1")
        paid = sum(r["payout"] for r in st["settlements"])
        self.assertEqual(paid + st["carry_out"], 600)  # pot conservation
        after_total = w.get_balance("p1").available + w.get_balance("p2").available
        self.assertEqual(after_total, before_total + paid)  # credits landed

    def test_insufficient(self):
        w = funded("p1", amount=50)
        s, _ = live_svc(w)
        with self.assertRaises(ServiceError) as cm:
            s.place_bet("room1", "p1", "A", 100, "k1")
        self.assertEqual(cm.exception.code, "INSUFFICIENT_BALANCE")
        self.assertEqual(w.get_balance("p1").available, 50)  # untouched

    def test_idempotent_replay(self):
        w = funded("p1")
        s, _ = live_svc(w)
        r1 = s.place_bet("room1", "p1", "A", 100, "k1")
        r2 = s.place_bet("room1", "p1", "A", 100, "k1")
        self.assertEqual(r1["bet_id"], r2["bet_id"])
        self.assertEqual(w.get_balance("p1").available, 9900)  # debited once

    def test_key_reuse_conflict(self):
        w = funded("p1")
        s, _ = live_svc(w)
        s.place_bet("room1", "p1", "A", 100, "k1")
        with self.assertRaises(ServiceError) as cm:
            s.place_bet("room1", "p1", "B", 100, "k1")
        self.assertEqual(cm.exception.code, "DUPLICATE_REQUEST")

    def test_cancel_refunds(self):
        w = funded("p1")
        s, _ = live_svc(w)
        s.place_bet("room1", "p1", "A", 100, "k1")
        out = s.cancel_round("room1", "test", "admin")
        self.assertEqual(out["voided"], 1)
        self.assertEqual(w.get_balance("p1").available, 10000)


class TestSession(unittest.TestCase):
    def test_token_single_use(self):
        s = TeenPattiService(config=LIVE, wallet=funded("p1"))
        tok = s.tokens.mint("p1", "room1", "teen-patti-pro")
        s.open_session(tok.token)
        with self.assertRaises(ServiceError):
            s.open_session(tok.token)  # replay -> INVALID_TOKEN

    def test_wrong_game_token(self):
        s = TeenPattiService(config=LIVE, wallet=funded("p1"))
        tok = s.tokens.mint("p1", "room1", "greedy")
        with self.assertRaises(ServiceError):
            s.open_session(tok.token)

    def test_reconnect(self):
        w = funded("p1")
        s, sess = live_svc(w)
        rc = s.reconnect(sess["session_id"], 0)
        self.assertIn("snapshot", rc)
        self.assertTrue(len(rc["missed_events"]) >= 1)


if __name__ == "__main__":
    unittest.main()
