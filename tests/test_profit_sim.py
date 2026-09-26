"""Tests for the Profit & Risk simulator.

The numbers here drive an operator's decision to change live pricing, so the
tests pin the two properties that are easy to get wrong and expensive to get
wrong: the projection must be reproducible, and it must never report a risk
signal that is nonsense (a negative giveback, or "Medium" for a configuration
that loses the house money).
"""
import unittest

from common.profit_sim import DEFAULT_ROUNDS, MAX_ROUNDS, simulate


class DeterminismTest(unittest.TestCase):
    def test_same_seed_same_result(self):
        cfg = {"base_house_edge_pct": 8.0, "max_payout_per_round": 10000,
               "rng_weight": 1.0}
        self.assertEqual(simulate(cfg), simulate(cfg))

    def test_different_seed_differs(self):
        cfg = {"base_house_edge_pct": 8.0, "max_payout_per_round": 10000,
               "rng_weight": 1.0}
        self.assertNotEqual(simulate(cfg, seed=1), simulate(cfg, seed=2))

    def test_reports_that_it_is_deterministic(self):
        self.assertTrue(simulate(rounds=50)["model"]["deterministic"])
        self.assertIn("not_a_measurement", simulate(rounds=50)["model"])


class BoundsTest(unittest.TestCase):
    def test_rounds_are_clamped(self):
        self.assertEqual(simulate(rounds=0)["rounds"], 1)
        self.assertEqual(simulate(rounds=10 ** 9)["rounds"], MAX_ROUNDS)

    def test_default_rounds_is_ten_thousand(self):
        self.assertEqual(DEFAULT_ROUNDS, 10_000)
        self.assertEqual(simulate()["rounds"], 10_000)

    def test_out_of_range_config_is_clamped_not_rejected(self):
        # The DB enforces ranges, but a hand-typed request must not be able to
        # produce a wild projection.
        r = simulate({"base_house_edge_pct": 999, "max_payout_per_round": -5,
                      "rng_weight": 50}, rounds=200)
        self.assertEqual(r["config_used"]["base_house_edge_pct"], 30.0)
        self.assertEqual(r["config_used"]["max_payout_per_round"], 100)
        self.assertEqual(r["config_used"]["rng_weight"], 3.0)

    def test_non_numeric_config_falls_back_to_defaults(self):
        r = simulate({"base_house_edge_pct": "abc", "rng_weight": None}, rounds=100)
        self.assertEqual(r["config_used"]["base_house_edge_pct"], 8.0)
        self.assertEqual(r["config_used"]["rng_weight"], 1.0)

    def test_missing_config_uses_defaults(self):
        r = simulate(None, rounds=100)
        self.assertEqual(r["config_used"]["base_house_edge_pct"], 8.0)
        self.assertEqual(r["config_used"]["max_payout_per_round"], 10000)


class SanityTest(unittest.TestCase):
    """Invariants that must hold for any configuration."""

    CONFIGS = [
        {"base_house_edge_pct": 8.0, "max_payout_per_round": 10000, "rng_weight": 1.0},
        {"base_house_edge_pct": 25.0, "max_payout_per_round": 200, "rng_weight": 3.0},
        {"base_house_edge_pct": 0.5, "max_payout_per_round": 1_000_000, "rng_weight": 0.1},
        {"base_house_edge_pct": 30.0, "max_payout_per_round": 100, "rng_weight": 2.0},
    ]

    def test_giveback_is_never_negative(self):
        # Regression: jackpot payouts were not deducted from the theoretical
        # take, so an aggressive config reported -158% giveback.
        for cfg in self.CONFIGS:
            r = simulate(cfg, rounds=800)
            self.assertGreaterEqual(r["cap_giveback"], 0.0, cfg)
            self.assertGreaterEqual(r["cap_giveback_pct"], 0.0, cfg)

    def test_risk_level_is_one_of_three(self):
        for cfg in self.CONFIGS:
            self.assertIn(simulate(cfg, rounds=400)["risk_level"],
                          {"Low", "Medium", "High"}, cfg)

    def test_losing_configuration_is_high_risk(self):
        # A payout cap far below the pot must not report a calm risk level.
        r = simulate({"base_house_edge_pct": 2.0, "max_payout_per_round": 100,
                      "rng_weight": 3.0}, rounds=3000)
        if r["expected_profit"] < 0:
            self.assertEqual(r["risk_level"], "High")

    def test_identity_holds(self):
        for cfg in self.CONFIGS:
            r = simulate(cfg, rounds=600)
            self.assertAlmostEqual(
                r["expected_profit"], r["total_staked"] - r["total_payout"], places=1)

    def test_exposure_never_exceeds_the_cap(self):
        for cap in (100, 500, 5000):
            r = simulate({"max_payout_per_round": cap, "base_house_edge_pct": 5.0},
                         rounds=500)
            self.assertLessEqual(r["max_exposure"], float(cap))

    def test_higher_edge_raises_profit(self):
        low = simulate({"base_house_edge_pct": 2.0, "max_payout_per_round": 10 ** 9},
                       rounds=1000)
        high = simulate({"base_house_edge_pct": 20.0, "max_payout_per_round": 10 ** 9},
                        rounds=1000)
        self.assertGreater(high["expected_profit"], low["expected_profit"])

    def test_tighter_cap_lowers_exposure_and_raises_house_revenue(self):
        # max_payout_per_round is a *risk* control, not a profit control: it
        # caps what the house can pay out, so tightening it shrinks the worst
        # case AND increases what the house retains. Asserting that a tighter
        # cap lowers profit would be asserting the opposite of how the
        # parameter behaves; the operator tuning it needs both effects visible.
        loose = simulate({"max_payout_per_round": 10 ** 9, "base_house_edge_pct": 8.0},
                         rounds=1000)
        tight = simulate({"max_payout_per_round": 150, "base_house_edge_pct": 8.0},
                         rounds=1000)
        self.assertLess(tight["max_exposure"], loose["max_exposure"])
        self.assertGreater(tight["expected_profit"], loose["expected_profit"])
        self.assertGreater(tight["capped_rounds"], loose["capped_rounds"])

    def test_model_block_lists_assumptions(self):
        m = simulate(rounds=10)["model"]
        self.assertEqual(m["kind"], "monte_carlo_projection")
        self.assertTrue(m["assumptions"])
        self.assertIn("seed", m)

    def test_round_counters_are_consistent(self):
        r = simulate(rounds=1000)
        self.assertLessEqual(r["capped_rounds"], 1000)
        self.assertLessEqual(r["jackpots"], 1000)
        self.assertLessEqual(r["losing_rounds"], 1000)
        self.assertLessEqual(r["worst_round"], 0.0)
        self.assertGreaterEqual(r["best_round"], r["worst_round"])


if __name__ == "__main__":
    unittest.main()
