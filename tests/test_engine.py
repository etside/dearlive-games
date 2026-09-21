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
        ok, high = __import__("games.teen_patti_pro.engine", fromlist=["x"])._is_sequence([14, 2, 3])
        self.assertTrue(ok and high == 3)

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


if __name__ == "__main__":
    unittest.main()
