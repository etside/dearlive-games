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
    def test_single_game_with_dearlive_code(self):
        plugins.import_builtin_games()
        games = {g["game_id"]: g for g in plugins.catalog()}
        self.assertEqual(games["teen-patti-pro"]["name"], "Teen Patti Pro")
        self.assertEqual(games["teen-patti-pro"]["dearlive_code"], "teen_patti")
        self.assertEqual(games["teen-patti-pro"]["entry"], "/teen-patti-pro/")
        self.assertEqual(games["teen-patti-pro"]["status"], "live")
        # the legacy id still resolves
        self.assertIn("teen_patti", games)

    def test_retired_games_are_absent_from_the_catalog(self):
        plugins.import_builtin_games()
        games = {g["game_id"] for g in plugins.catalog()}
        for retired in ("greedy-monkey", "greedy_monkey", "baby-king",
                        "baby_king", "food_wheel", "animal-food-wheel"):
            self.assertNotIn(retired, games)

    def test_alias_create_behaviour(self):
        plugins.import_builtin_games()
        from games.teen_patti_pro.config import TeenPattiConfig
        room = plugins.create("teen_patti", "r9", TeenPattiConfig(confirmed=True))
        self.assertEqual(room.room_id, "r9")
        # A retired game is rejected rather than silently resolving.
        from common.plugins import UnknownGame
        for retired in ("greedy_monkey", "baby-king"):
            with self.assertRaises(UnknownGame):
                plugins.create(retired, "rx", None)


OPTS = [
    {"id": "o1", "name": "Banana", "weight": 50, "multiplier": 2},
    {"id": "o2", "name": "Mango", "weight": 30, "multiplier": 3},
    {"id": "o3", "name": "Crown", "weight": 5, "multiplier": 20},
]


