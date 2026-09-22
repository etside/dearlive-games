"""Wheel service tests (G1 Greedy Monkey + G2 Baby King, BRD G1/G2 FR).

Full flow per game: session -> balance -> state/options -> bet (unique
bet_id + wallet txn ref, atomic debit) -> totals -> close (server time) ->
authoritative HMAC result -> settlement (exactly-once, stake x multiplier)
-> history/recent/earnings/reconnect. Plus: invalid/insufficient/late
rejections, idempotent replay, cancel/refund, autobet, TBC gate.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.lifecycle import RoundStatus
from games.wheel_common.configs import baby_king_config, greedy_config
from games.wheel_common.service import ServiceError, WheelService
from integrations.dearlive_mock import (MockDearLiveSessions, MockDearLiveTokens,
                                        MockDearLiveWallet)


def make(game="greedy", confirmed=True):
    cfg = greedy_config() if game == "greedy" else baby_king_config()
    cfg.confirmed = confirmed
    w = MockDearLiveWallet()
    svc = WheelService(config=cfg, wallet=w, tokens=MockDearLiveTokens(),
                       sessions=MockDearLiveSessions())
    return svc, w


class WheelFlow(unittest.TestCase):
    def test_greedy_full_flow(self):
        svc, w = make("greedy")
        for p in ("a", "b"):
            w.fund(p, 5000)
        sa = svc.open_session(svc.tokens.mint("a", "r1", "greedy-monkey").token)
        sb = svc.open_session(svc.tokens.mint("b", "r1", "greedy-monkey").token)
        self.assertEqual(sa["game_id"], "greedy-monkey")
        st0 = svc.start_round("r1")
        self.assertEqual(st0["status"], "BETTING_OPEN")
        # configured options with multipliers + totals
        st = svc.state("r1", "a")
        self.assertTrue(len(st["options"]) >= 3)
        self.assertTrue(all("multiplier" in o and "hot" in o for o in st["options"]))
        # unique round number + synchronized countdown
        self.assertEqual(st["round_no"], 1)
        self.assertGreater(st["betting_end_at"], st["serverTime"])
        # bets: denomination + option validation
        b1 = svc.place_bet("r1", "a", "banana", 100, "k1")
        b2 = svc.place_bet("r1", "b", "crown", 500, "k2")
        self.assertNotEqual(b1["bet_id"], b2["bet_id"])
        self.assertEqual(w.get_balance("a").available, 4900)
        # totals: Total Bet + My Total Bet
        st = svc.state("r1", "a")
        self.assertEqual(st["total_bet"], 600)
        self.assertEqual(st["my_total_bet"], 100)
        self.assertEqual(st["totals"], {"banana": 100, "crown": 500})
        # idempotent replay: no second debit
        r = svc.place_bet("r1", "a", "banana", 100, "k1")
        self.assertEqual(r["bet_id"], b1["bet_id"])
        self.assertEqual(w.get_balance("a").available, 4900)
        # invalid option / denom / insufficient / missing key
        with self.assertRaises(ServiceError):
            svc.place_bet("r1", "a", "nope", 100, "kx")
        with self.assertRaises(ServiceError):
            svc.place_bet("r1", "a", "banana", 7, "ky")
        with self.assertRaises(ServiceError):
            svc.place_bet("r1", "a", "banana", 100000, "kz")
        with self.assertRaises(ServiceError):
            svc.place_bet("r1", "a", "banana", 100, "")
        # server-controlled close -> authoritative result
        svc.close_betting("r1")
        with self.assertRaises(ServiceError):
            svc.place_bet("r1", "a", "banana", 100, "klate")
        res = svc.publish_result("r1")
        self.assertIn(res["winning_option_id"], [o.option_id for o in svc.config.options])
        # lifecycle passed through RESULT_PROCESSING
        kinds = [e["kind"] for e in svc._room("r1").round.events]
        self.assertIn("result.processing", kinds)
        # settlement against accepted bets, exactly once
        stl = svc.settle("r1")
        rows = {x["bet_id"]: x for x in stl["settlements"]}
        for bet_id, row in rows.items():
            wrow = [e for e in w.ledger if e["ref"] == f"settle:{bet_id}"]
            if row["payout"] > 0:
                self.assertEqual(len(wrow), 1)  # credited exactly once
        # conservation: stakes == paid + lost
        staked = 600
        paid = sum(x["payout"] for x in stl["settlements"])
        total = sum(w.get_balance(p).available for p in ("a", "b"))
        self.assertEqual(total, 10000 - staked + paid)
        again = svc.settle("r1")
        self.assertEqual(sum(w.get_balance(p).available for p in ("a", "b")), total)
        # recent-result strip + history + earnings + reconnect
        self.assertEqual(svc.recent_results("r1")["results"][0]["round_id"],
                         stl["round_id"])
        h = svc.history("r1", "a")
        self.assertEqual(len(h["bets"]), 1)
        self.assertIn("net", h["earnings_today"])
        rec = svc.reconnect(sa["session_id"], 0)
        self.assertIn("snapshot", rec)
        self.assertTrue(rec["missed_events"])
        # audit trail exists
        acts = {e["action"] for e in svc.audit.entries}
        self.assertTrue({"session.open", "round.start", "bet.place",
                         "result.publish", "settlement.credit"} <= acts)

    def test_baby_king_flow(self):
        svc, w = make("king")
        w.fund("p", 2000)
        svc.open_session(svc.tokens.mint("p", "rk", "baby-king").token)
        svc.start_round("rk")
        svc.place_bet("rk", "p", "lion", 500, "kb1")
        svc.close_betting("rk")
        res = svc.publish_result("rk")
        self.assertTrue(res["winning_label"])
        stl = svc.settle("rk")
        self.assertEqual(len(stl["settlements"]), 1)
        row = stl["settlements"][0]
        if row["option_id"] == res["winning_option_id"]:
            self.assertEqual(row["payout"], int(500 * res["payout_multiplier"]))
        else:
            self.assertEqual(row["payout"], 0)

    def test_cancel_refunds(self):
        svc, w = make("greedy")
        w.fund("p", 1000)
        svc.open_session(svc.tokens.mint("p", "rc", "greedy-monkey").token)
        svc.start_round("rc")
        svc.place_bet("rc", "p", "apple", 100, "kc")
        out = svc.cancel_round("rc", "ops", "admin")
        self.assertEqual(out["voided"], 1)
        self.assertEqual(w.get_balance("p").available, 1000)

    def test_autobet_where_approved(self):
        svc, w = make("greedy")
        svc.config.auto_allowed = True
        w.fund("auto_p", 5000)
        svc.open_session(svc.tokens.mint("auto_p", "ra", "greedy-monkey").token)
        cfg = svc.set_autobet("ra", "auto_p", "mango", 100, 2)
        self.assertEqual(cfg["rounds_left"], 2)
        svc.start_round("ra")  # autobet fires server-side
        st = svc.state("ra", "auto_p")
        self.assertEqual(st["my_total_bet"], 100)
        self.assertEqual(w.get_balance("auto_p").available, 4900)
        # unapproved game rejects
        svc2, _ = make("king")
        with self.assertRaises(ServiceError):
            svc2.set_autobet("rx", "p", "lion", 100, 1)

    def test_tbc_gate(self):
        svc, w = make("greedy", confirmed=False)
        w.fund("p", 1000)
        svc.open_session(svc.tokens.mint("p", "rt", "greedy-monkey").token)
        svc.start_round("rt")
        with self.assertRaises(ServiceError):
            svc.place_bet("rt", "p", "banana", 100, "kt")

    def test_sweep_closes_settles(self):
        svc, w = make("greedy")
        w.fund("p", 1000)
        svc.open_session(svc.tokens.mint("p", "rs", "greedy-monkey").token)
        svc.start_round("rs")
        svc.place_bet("rs", "p", "banana", 100, "ks")
        r = svc._room("rs").round
        r.betting_end_at_ms = svc._now() - 1
        rep = svc.sweep()
        self.assertEqual(rep[0]["actions"], ["closed", "result", "settled"])
        self.assertEqual(svc._room("rs").round.status, RoundStatus.CLOSED)


if __name__ == "__main__":
    unittest.main()
