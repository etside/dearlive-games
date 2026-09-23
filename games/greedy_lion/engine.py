"""Greedy Lion — wheel outcome engine (parity with DearLive current).

Same logic as Uradhura `WheelDriver` code `greedy_lion`: weighted
HMAC-SHA256 pick + deterministic landing angle (see common/wheel.py).
Options (lion-themed weights + multipliers + icon/colorHex) are
operator-configured in DearLive; the engine only resolves the committed
seed into an outcome. Authoritative play runs through the generic
WheelService (rounds, timer, bets, settlement); see plugin status/TBC.
"""
from common.engine import CommonGameEngine
from common.wheel import wheel_outcome

GAME_ID = "greedy-lion"


def spin(options, server_seed: str, client_seed: str, nonce: int) -> dict:
    """Pure outcome: weighted pick + angle. Deterministic per seed."""
    return wheel_outcome(options, server_seed, client_seed, nonce)


def _todo(*args, **kwargs):
    raise NotImplementedError("Greedy Lion betting via plugins.create is not the play path — use WheelService")


class GreedyLionEngine(CommonGameEngine):
    game_id = GAME_ID


for _m in ("createSession", "joinSession", "leaveSession", "getState",
           "validateAction", "applyAction", "startRound", "endRound",
           "calculateResult", "settle", "cancel", "handleTimeout",
           "handleReconnect"):
    setattr(GreedyLionEngine, _m, staticmethod(_todo))

# Back-compat name.
LionEngine = GreedyLionEngine
