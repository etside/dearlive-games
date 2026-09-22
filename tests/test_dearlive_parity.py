"""Parity with DearLive current (Uradhura): names, wheel engine, Teen Patti ranks.

- Catalog shows Teen Patti Pro / Greedy Monkey / Baby King with entry +
  dearlive_code; old ids resolve as aliases.
- Wheel outcome (Greedy Monkey + Baby King) is deterministic, weight-aware,
  angle-bounded, and matches an independent HMAC-SHA256 computation
  (mirrors FairRandom/WheelDriver).
- Teen Patti ranking matches TeenPattiDriver: trail > pure_seq > seq >
  color > pair > high; A-2-3 is a (low) sequence.
"""
import hashlib
import hmac
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import plugins
from games.teen_patti_pro.engine import evaluate_hand


def indep_float(server_seed, client_seed, nonce, instance=0):
    mac = hmac.new(server_seed.encode(),
                   f"{client_seed}:{nonce}:{instance}".encode(),
                   hashlib.sha256).digest()
    return int.from_bytes(mac[:7], "big") / 2 ** 56


class TestCatalogNames(unittest.TestCase):
    def test_three_games_with_dearlive_codes(self):
        plugins.import_builtin_games()
        games = {g["game_id"]: g for g in plugins.catalog()}
        self.assertEqual(games["teen-patti-pro"]["name"], "Teen Patti Pro")
        self.assertEqual(games["teen-patti-pro"]["dearlive_code"], "teen_patti")
        self.assertEqual(games["greedy-monkey"]["name"], "Greedy Monkey")
        self.assertEqual(games["greedy-monkey"]["dearlive_code"], "greedy_monkey")
        self.assertEqual(games["baby-king"]["name"], "Baby King")
        self.assertEqual(games["baby-king"]["dearlive_code"], "food_wheel")
        self.assertEqual(games["teen-patti-pro"]["entry"], "/teen-patti-pro/")
        # legacy ids still resolve
        for old in ("teen_patti", "greedy", "greedy_monkey",
                    "animal-food-wheel", "food-wheel", "food_wheel"):
            self.assertIn(old, games)
        self.assertEqual(games["teen-patti-pro"]["status"], "live")
        self.assertEqual(games["greedy-monkey"]["status"], "planned")
        self.assertEqual(games["baby-king"]["status"], "planned")

    def test_alias_create_behaviour(self):
        plugins.import_builtin_games()
        from games.teen_patti_pro.config import TeenPattiConfig
        room = plugins.create("teen_patti", "r9", TeenPattiConfig(confirmed=True))
        self.assertEqual(room.room_id, "r9")
        with self.assertRaises(plugins.GameDisabled):
            plugins.create("greedy_monkey")
        with self.assertRaises(plugins.GameDisabled):
            plugins.create("baby-king")


OPTS = [
    {"id": "o1", "name": "Banana", "weight": 50, "multiplier": 2},
    {"id": "o2", "name": "Mango", "weight": 30, "multiplier": 3},
    {"id": "o3", "name": "Crown", "weight": 5, "multiplier": 20},
]


class TestWheelParity(unittest.TestCase):
    def test_deterministic_and_hmac_independent(self):
        from games.greedy.engine import spin as monkey
        from games.animal_wheel.engine import spin as king
        a = monkey(OPTS, "srv", "cli", 7)
        b = monkey(OPTS, "srv", "cli", 7)
        self.assertEqual(a, b)
        c = king(OPTS, "srv", "cli", 7)
        self.assertEqual(a["winning_option_id"], c["winning_option_id"])
        # independent pick computation
        total = 85.0
        pick = indep_float("srv", "cli", 7, 0) * total
        cum, exp = 0.0, 2
        for i, w in enumerate((50, 30, 5)):
            cum += w
            if pick < cum:
                exp = i
                break
        self.assertEqual(a["winning_index"], exp)
        self.assertGreaterEqual(a["angle"], exp * 120.0)
        self.assertLess(a["angle"], exp * 120.0 + 120.0)
        self.assertEqual(a["result_text"],
                         f"{OPTS[exp]['name']} x{float(OPTS[exp]['multiplier']):.2f}")

    def test_empty_options_rejected(self):
        from games.greedy.engine import spin
        with self.assertRaises(ValueError):
            spin([], "s", "c", 1)


class TestTeenPattiRankParity(unittest.TestCase):
    def cat(self, hand):
        return evaluate_hand(hand)[0]

    def test_hierarchy_matches_driver(self):
        trail = [(14, "S"), (14, "H"), (14, "D")]
        pure = [(9, "S"), (10, "S"), (11, "S")]
        seq = [(9, "S"), (10, "H"), (11, "D")]
        color = [(14, "H"), (10, "H"), (3, "H")]
        pair = [(13, "S"), (13, "H"), (2, "D")]
        high = [(14, "S"), (11, "H"), (4, "D")]
        self.assertEqual([self.cat(h) for h in (high, pair, color, seq, pure, trail)],
                         [1, 2, 3, 4, 5, 6])

    def test_ace_low_straight_is_low_sequence(self):
        from games.teen_patti_pro.engine import _is_sequence
        ok, tb = _is_sequence([14, 2, 3], "lowest")
        self.assertTrue(ok)
        # A-2-3 (tiebreak high=3) loses to 2-3-4
        low = evaluate_hand([(14, "S"), (2, "H"), (3, "D")])
        mid = evaluate_hand([(2, "S"), (3, "H"), (4, "D")])
        self.assertEqual(low[0], mid[0] == 4 and 4)
        self.assertLess(low, mid)


if __name__ == "__main__":
    unittest.main()
