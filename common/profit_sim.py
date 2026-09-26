"""Monte Carlo projection for the Profit & Risk Engine.

What this is
------------
The admin "Simulate Profit Scenario" button runs this. It projects house
profit over a number of virtual rounds from the *configured* risk parameters,
so an operator can see the effect of a proposed change before saving it.

What this is not
----------------
It is a **model, not a measurement**. It says nothing about what a given
configuration actually earned; only `dashboard_kpis()` reports that. The
response therefore carries an explicit ``model`` block describing every
assumption, and the UI is expected to render it next to the numbers. A
projection shown without its assumptions is how a risk tool starts being
trusted for the wrong thing.

The model
---------
Each virtual round:

1. Draw a seat count from 2..max_seats, and a stake per seat from a uniform
   distribution over the configured denomination band. Both are deliberately
   crude: the point is to show the *shape* of the risk, not to forecast.
2. Pot = sum of stakes.
3. House take = pot x house_edge, i.e. the edge is a fraction of the action.
4. Payout = pot - take, then clamped to ``max_payout_per_round``. Clamping is
   where the interesting risk lives: a capped round returns more than the edge
   would predict, and that shortfall is reported as ``capped_rounds``.
5. ``rng_weight`` scales a jackpot-style payout multiplier. It is a frequency
   knob, not a size knob, so it is applied to how often the multiplier fires.

Outputs are aggregates plus the two risk signals an operator acts on: the
worst single-round exposure, and how much profit the payout cap gave back.
"""
import random
from typing import Any, Dict, List, Optional

DEFAULT_ROUNDS = 10_000
MAX_ROUNDS = 200_000          # guard: a UI click must not wedge a worker


def simulate(config: Optional[Dict[str, Any]] = None, rounds: int = DEFAULT_ROUNDS,
             seed: int = 20260926, min_bet: int = 20, max_bet: int = 1000,
             max_seats: int = 3) -> Dict[str, Any]:
    """Project house profit over ``rounds`` virtual rounds.

    ``seed`` is fixed by default so the same configuration always produces the
    same projection. An operator comparing two settings needs the difference to
    come from the settings, not from sampling noise; a random seed would make
    every comparison unreliable.
    """
    cfg = dict(config or {})
    edge_pct = _clamp(_f(cfg.get("base_house_edge_pct"), 8.0), 0.0, 30.0)
    max_payout = int(_clamp(_f(cfg.get("max_payout_per_round"), 10000), 100, 1_000_000))
    rng_weight = _clamp(_f(cfg.get("rng_weight"), 1.0), 0.1, 3.0)

    rounds = (DEFAULT_ROUNDS if rounds is None
              else max(1, min(int(rounds), MAX_ROUNDS)))
    max_seats = max(2, min(int(max_seats or 3), 9))
    min_bet = max(1, int(min_bet or 1))
    max_bet = max(min_bet, int(max_bet or min_bet))

    rng = random.Random(seed)
    edge = edge_pct / 100.0
    jackpot_chance = min(0.25, 0.05 * rng_weight)   # 5% at weight 1.0

    total_staked = 0
    total_payout = 0
    jackpot_cost = 0
    capped_rounds = 0
    jackpots = 0
    worst_exposure = 0
    per_round: List[float] = []

    for _ in range(rounds):
        seats = rng.randint(2, max_seats)
        pot = sum(rng.randint(min_bet, max_bet) for _ in range(seats))
        total_staked += pot

        take = pot * edge
        payout = pot - take

        if rng.random() < jackpot_chance:
            jackpots += 1
                # The jackpot is money leaving the house, so it is tracked
                # separately rather than folded into the edge expectation --
                # otherwise "giveback" goes negative on aggressive settings,
                # which reads as nonsense.
            jackpot_cost += pot * 0.5
            payout += pot * 0.5

        if payout > max_payout:
            payout = float(max_payout)
            capped_rounds += 1

        total_payout += payout
        per_round.append(pot - payout)
        if payout > worst_exposure:
            worst_exposure = payout

    expected_profit = total_staked - total_payout
    roi = (expected_profit / total_staked * 100.0) if total_staked else 0.0

    # Risk: how much of the *intended* take (edge plus jackpot cost) the
    # payout cap surrendered, plus how aggressive the knobs are. A high edge
    # with a tight cap is the combination that loses money, so both feed the
    # level. Clamped at zero because a giveback cannot be negative -- a
    # negative figure means the configuration out-earned its own assumption,
    # which is reported by expected_profit instead.
    theoretical_take = total_staked * edge + jackpot_cost
    giveback = max(0.0, theoretical_take - expected_profit)
    giveback_ratio = (giveback / theoretical_take * 100.0) if theoretical_take else 0.0
    risk_level = _risk_level(giveback_ratio, rng_weight, edge_pct,
                             expected_profit, total_staked)

    losses = [p for p in per_round if p < 0]
    return {
        "rounds": rounds,
        "expected_profit": round(expected_profit, 2),
        "roi_pct": round(roi, 2),
        "max_exposure": round(float(worst_exposure), 2),
        "total_staked": total_staked,
        "total_payout": round(total_payout, 2),
        "theoretical_take": round(theoretical_take, 2),
        "jackpot_cost": round(jackpot_cost, 2),
        "cap_giveback": round(giveback, 2),
        "cap_giveback_pct": round(giveback_ratio, 2),
        "capped_rounds": capped_rounds,
        "capped_rounds_pct": round(capped_rounds / rounds * 100.0, 2),
        "jackpots": jackpots,
        "losing_rounds": len(losses),
        "worst_round": round(min(per_round), 2) if per_round else 0.0,
        "best_round": round(max(per_round), 2) if per_round else 0.0,
        "risk_level": risk_level,
        "config_used": {
            "base_house_edge_pct": edge_pct,
            "max_payout_per_round": max_payout,
            "rng_weight": rng_weight,
            "min_bet": min_bet, "max_bet": max_bet, "max_seats": max_seats,
        },
        "model": {
            "kind": "monte_carlo_projection",
            "seed": seed,
            "deterministic": True,
            "not_a_measurement": (
                "Projection from configured parameters, not realised results. "
                "Use GET /api/v1/admin/dashboard for actuals."
            ),
            "assumptions": [
                "seats per round drawn uniformly from 2..max_seats",
                "stake per seat drawn uniformly over [min_bet, max_bet]",
                "house edge applied as a fraction of the pot",
                "payout clamped to max_payout_per_round",
                "jackpot fires with probability min(0.25, 0.05 * rng_weight) "
                "and adds half the pot",
            ],
        },
    }


def _risk_level(giveback_ratio: float, rng_weight: float, edge_pct: float,
                expected_profit: float, total_staked: float) -> str:
    """Low / Medium / High.

    Losing money is High regardless of how attractive the other knobs look, so
    that check comes first -- a configuration can post a healthy-looking edge
    and still lose the house money once the payout cap binds.
    """
    if total_staked and expected_profit < 0:
        return "High"
    if giveback_ratio >= 50.0 or edge_pct <= 1.0:
        return "High"
    if giveback_ratio >= 20.0 or rng_weight >= 2.0 or edge_pct >= 25.0:
        return "Medium"
    return "Low"


def _f(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
