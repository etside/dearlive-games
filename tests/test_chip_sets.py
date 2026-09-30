"""Chip denominations are table configuration, resolved on the server.

The client must never pick the ladder: if it offered a chip the server
refuses, every bet on that chip comes back VALIDATION_ERROR. So the ladder
lives in config, the snapshot publishes both the ladder and its name, and
the client renders whatever `denoms` it is handed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from games.teen_patti_pro.config import CHIP_SETS, denoms_for  # noqa: E402

CLIENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "games", "teen_patti_pro", "client", "game.js")


class ChipSetTest(unittest.TestCase):
    def setUp(self):
        with open(CLIENT, encoding="utf-8") as fh:
            self.js = fh.read()

    def test_low_set_fits_the_demo_balance(self):
        # A 10,000 demo wallet cannot cover a single 100K chip, so the low
        # ladder has to stay small or the table is unplayable on arrival.
        self.assertEqual(denoms_for("low"), (20, 100, 500, 1000))
        self.assertLessEqual(max(denoms_for("low")), 1000)

    def test_high_set_is_the_banked_ladder(self):
        self.assertEqual(denoms_for("high"), (1000, 10_000, 50_000, 100_000))

    def test_an_unknown_chip_set_falls_back_to_low(self):
        for junk in ("", None, "nonsense", "HIGH ", 7):
            self.assertEqual(denoms_for(junk), CHIP_SETS["low"],
                             "a bad chip_set must not take the table down")

    def test_explicit_denominations_win(self):
        self.assertEqual(denoms_for("low", (5, 25)), (5, 25))

    def test_both_ladders_are_sorted_and_positive(self):
        for name, ds in CHIP_SETS.items():
            self.assertEqual(list(ds), sorted(ds), "%s must ascend" % name)
            self.assertTrue(all(d > 0 for d in ds), "%s must be positive" % name)

    def test_client_renders_server_denoms_not_a_local_ladder(self):
        self.assertIn("DENOMS = next;", self.js,
                      "the client must adopt the server's denominations")
        self.assertIn("if (!next.length) return false;", self.js,
                      "a snapshot without denoms must not clear the chips")
        for hardcoded in ("[10000, 50000, 100000]", "(1000, 10000, 50000, 100000)"):
            self.assertNotIn(hardcoded, self.js,
                             "the client must not hardcode a chip ladder")


if __name__ == "__main__":
    unittest.main()
