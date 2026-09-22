"""Engine unit tests: determinism, ranking, lifecycle, settle math."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.lifecycle import RoundStatus, LifecycleError, transition
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.engine import Room, evaluate_hand

CONF = TeenPattiConfig(confirmed=True)
T0 = 1_700_000_000_000


def live_room(seed="ab" * 16):
    room = Room("test", CONF)
    room.start_round(T0, seed_hex=seed)
    return room


class TestRanking(unittest.TestCase):
    def test_order(self):
        trail = evaluate_hand([(14, "S"), (14, "H"), (14, "D")])
        pure = evaluate_hand([(14, "S"), (13, "S"), (12, "S")])
        seq = evaluate_hand([(9, "S"), (8, "H"), (7, "D")])
        color = evaluate_hand([(14, "S"), (10, "S"), (4, "S")])
        pair = evaluate_hand([(13, "S"), (13, "H"), (2, "D")])
        high = evaluate_hand([(14, "S"), (11, "H"), (5, "D")])
        self.assertTrue(trail > pure > seq > color > pair > high)

    def test_ace_low_straight(self):
        eng = __import__("games.teen_patti_pro.engine", fromlist=["x"])
        ok, tb = eng._is_sequence([14, 2, 3], "lowest")
        self.assertTrue(ok and tb == (3,))
        # Reference-algorithm modes (compare report §4):
        ok, tb = eng._is_sequence([14, 2, 3], "second")  # esrrhs: just below A-K-Q
        self.assertTrue(ok and tb == (14, 3, 2))
        self.assertTrue(eng.evaluate_hand([(14, "S"), (2, "H"), (3, "D")], "second")
                        < eng.evaluate_hand([(14, "S"), (13, "H"), (12, "D")], "second"))
        self.assertTrue(eng.evaluate_hand([(14, "S"), (2, "H"), (3, "D")], "second")
                        > eng.evaluate_hand([(13, "S"), (12, "H"), (11, "D")], "second"))
        ok, tb = eng._is_sequence([14, 2, 3], "highest")  # traditional: above A-K-Q
        self.assertTrue(ok and tb == (15,))
        # Default mode keeps A-2-3 the lowest straight.
        self.assertTrue(eng.evaluate_hand([(14, "S"), (2, "H"), (3, "D")])
                        < eng.evaluate_hand([(4, "S"), (3, "H"), (2, "D")]))

    def test_deal_uniqueness(self):
        """9 dealt cards are always distinct (single 52-deck, no replacement)."""
        room = Room("deal", CONF)
        for seed in ("00" * 16, "ff" * 16, "ab" * 16):
            r = room.start_round(T0, seed_hex=seed)
            all_cards = [c for h in r.hands.values() for c in h]
            self.assertEqual(len(all_cards), 9)
            self.assertEqual(len(set(all_cards)), 9)
            room.close_betting(T0 + 1)
            room.calculate_result(T0 + 2)
            room.settle(T0 + 3)

    def test_pair_kicker(self):
        a = evaluate_hand([(13, "S"), (13, "H"), (14, "D")])
        b = evaluate_hand([(13, "D"), (13, "C"), (2, "S")])
        self.assertTrue(a > b)


class TestLifecycle(unittest.TestCase):
    def test_illegal(self):
        with self.assertRaises(LifecycleError):
            transition(RoundStatus.UPCOMING, RoundStatus.RESULT)

    def test_full_cycle(self):
        room = live_room()
        room.close_betting(T0 + 1000)
        room.calculate_result(T0 + 2000)
        rows = room.settle(T0 + 3000)
        self.assertEqual(room.round.status, RoundStatus.CLOSED)
        self.assertEqual(rows, room.settle(T0 + 4000))  # replay identical

    def test_bet_after_close_rejected(self):
        room = live_room()
        room.close_betting(T0 + CONF.guess_ms + 1)
        with self.assertRaises(LifecycleError):
            room.place_bet("p", "A", 100, "k", T0 + CONF.guess_ms + 2)


class TestSettleMath(unittest.TestCase):
    def test_pot_takes_all_single_winner(self):
        room = live_room(seed="01" * 16)
        room.place_bet("p1", "A", 100, "k1", T0 + 10)
        room.place_bet("p2", "B", 500, "k2", T0 + 20)
        room.close_betting(T0 + CONF.guess_ms + 1)
        room.calculate_result(T0 + CONF.guess_ms + 2)
        rows = room.settle(T0 + CONF.guess_ms + 3)
        by_bet = {r["bet_id"]: r for r in rows}
        total = sum(r["payout"] for r in rows) + room.carry_over
        self.assertEqual(total, 600)  # conservation: payouts + carry == pot
        # winner side paid in full, loser zero
        w = set(room.round.winner_positions)
        for r in rows:
            bet = next(b for b in room.round.bets if b.bet_id == r["bet_id"])
            if bet.position in w:
                self.assertGreater(r["payout"], 0)
            else:
                self.assertEqual(r["payout"], 0)

    def test_conservation_fuzz(self):
        import random
        rng = random.Random(7)
        for trial in range(30):
            room = Room("f", CONF)
            room.start_round(T0, seed_hex=f"{trial:032x}")
            nb = rng.randint(0, 9)
            for i in range(nb):
                pos = "ABC"[rng.randint(0, 2)]
                amt = CONF.denoms[rng.randint(0, 3)]
                try:
                    room.place_bet(f"p{i%3}", pos, amt, f"k{trial}-{i}", T0 + i)
                except LifecycleError:
                    pass
            room.close_betting(T0 + CONF.guess_ms + 1)
            room.calculate_result(T0 + CONF.guess_ms + 2)
            rows = room.settle(T0 + CONF.guess_ms + 3)
            pot = sum(b.amount for b in room.round.bets) + room.round.carry_in
            self.assertEqual(sum(r["payout"] for r in rows) + room.carry_over, pot,
                             f"conservation failed trial {trial}")
            # unique settlement ids
            ids = [r["settlement_id"] for r in rows]
            self.assertEqual(len(ids), len(set(ids)))


class TestTieFairness(unittest.TestCase):
    """JEV money-out review: winners must never be paid less than staked."""

    def _forced_tie_room(self):
        room = Room("tie", CONF)
        r = room.start_round(T0, seed_hex="cc" * 16)
        # Force identical best hands on A and B (trail of Kings both).
        r.hands = {"A": [(13, "S"), (13, "H"), (13, "D")],
                   "B": [(13, "C"), (13, "S"), (13, "H")],
                   "C": [(2, "S"), (3, "H"), (5, "D")]}
        return room, r

    def test_tie_winners_never_lose(self):
        room, r = self._forced_tie_room()
        room.place_bet("pa", "A", 100, "k1", T0 + 10)    # light side
        room.place_bet("pb", "B", 1000, "k2", T0 + 20)   # heavy side x10 denom? use 500+500
        room.close_betting(T0 + CONF.guess_ms + 1)
        room.calculate_result(T0 + CONF.guess_ms + 2)
        self.assertEqual(set(r.winner_positions), {"A", "B"})
        rows = room.settle(T0 + CONF.guess_ms + 3)
        by_player = {x["player_id"]: x["payout"] for x in rows}
        # Dead heat: heavy side must NOT lose money on a win.
        self.assertGreaterEqual(by_player["pb"], 1000)
        self.assertGreaterEqual(by_player["pa"], 100)
        self.assertEqual(sum(x["payout"] for x in rows) + room.carry_over, 1100)


class TestJokers(unittest.TestCase):
    """Wild-joker support (reference getMax parity; TBC JOKER, default off)."""

    def test_disabled_by_default(self):
        self.assertEqual(CONF.jokers, 0)
        room = Room("noj", CONF)
        r = room.start_round(T0, seed_hex="dd" * 16)
        flat = [c for h in r.hands.values() for c in h]
        self.assertEqual(len(flat), 9)
        self.assertTrue(all(c != (0, "J") for c in flat))

    def test_triple_joker_is_aces(self):
        from games.teen_patti_pro.engine import best_expansion, JOKER
        self.assertEqual(best_expansion([JOKER, JOKER, JOKER]),
                         [(14, "S"), (14, "H"), (14, "D")])

    def test_double_joker_makes_trail(self):
        from games.teen_patti_pro.engine import best_expansion, JOKER, evaluate_hand
        for rank in (5, 14, 2):
            res = best_expansion([(rank, "C"), JOKER, JOKER])
            self.assertEqual(evaluate_hand(res)[0], 6)  # trail
            self.assertTrue(all(c[0] == rank for c in res))
            self.assertEqual(len(set(res)), 3)  # distinct cards

    def test_single_joker_pair_to_trail(self):
        from games.teen_patti_pro.engine import best_expansion, JOKER, evaluate_hand
        res = best_expansion([(13, "S"), (13, "H"), JOKER])
        self.assertEqual(res, [(13, "S"), (13, "H"), (13, "D")])
        self.assertEqual(evaluate_hand(res)[0], 6)

    def test_single_joker_best_straight_flush(self):
        from games.teen_patti_pro.engine import best_expansion, JOKER, evaluate_hand
        # 9S TS + joker -> Qs J? best is straight flush Q-high? K/Q/J... optimal: (12,11,10)?S?
        res = best_expansion([(9, "S"), (10, "S"), JOKER])
        cat, _ = evaluate_hand(res)
        self.assertEqual(cat, 5)  # straight flush (J/Q/K-high all cat 5; any is optimal)
        self.assertTrue(all(c[1] == "S" for c in res))

    def test_joker_round_end_to_end(self):
        cfg = TeenPattiConfig(confirmed=True, jokers=3)
        room = Room("jok", cfg)
        r = room.start_round(T0, seed_hex="ee" * 16)
        room.place_bet("p1", "A", 100, "k1", T0 + 10)
        room.close_betting(T0 + cfg.guess_ms + 1)
        room.calculate_result(T0 + cfg.guess_ms + 2)
        self.assertEqual(set(r.resolved), {"A", "B", "C"})
        for p, h in r.resolved.items():
            self.assertTrue(all(c != (0, "J") for c in h))  # fully expanded
        rows = room.settle(T0 + cfg.guess_ms + 3)
        self.assertEqual(sum(x["payout"] for x in rows) + room.carry_over, 100)

    def test_expansion_optimality_fuzz(self):
        """best_expansion must equal brute-force optimum over all substitutes."""
        import itertools
        from games.teen_patti_pro.engine import (best_expansion, build_deck, evaluate_hand,
                                                 JOKER)
        full = [c for c in build_deck() if c != JOKER]
        rng_cases = [[(14, "S"), (13, "H")], [(2, "C"), (7, "D")], [(9, "S"), (9, "H")],
                     [(5, "C"), (5, "D")], [(14, "S"), (2, "H")]]
        for plain in rng_cases:
            got = best_expansion(plain + [JOKER])
            best = max((evaluate_hand(plain + [c]) for c in full if tuple(c) not in plain))
            self.assertEqual(evaluate_hand(got), best, f"suboptimal for {plain}")


if __name__ == "__main__":
    unittest.main()
