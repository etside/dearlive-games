"""Game 2: Greedy Monkey — wheel outcome engine (parity with DearLive current).

Same logic as Uradhura `WheelDriver` code `greedy_monkey`: weighted
HMAC-SHA256 pick + deterministic landing angle (see common/wheel.py).
Options (foods + weights + multipliers + icon/colorHex) are
operator-configured in DearLive; the engine only resolves the committed
seed into an outcome.
"""
from common.engine import CommonGameEngine
from common.wheel import wheel_outcome

GAME_ID = "greedy-monkey"


def spin(options, server_seed: str, client_seed: str, nonce: int) -> dict:
    """Pure outcome: weighted pick + angle. Deterministic per seed."""
    return wheel_outcome(options, server_seed, client_seed, nonce)


class GreedyMonkeyEngine(CommonGameEngine):
    game_id = GAME_ID


# Back-compat name (previous stub).
GreedyEngine = GreedyMonkeyEngine
