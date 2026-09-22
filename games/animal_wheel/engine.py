"""Game 3: Baby King — wheel outcome engine (DearLive wheel slot).

Same deterministic logic as DearLive current wheel games (`WheelDriver`:
weighted HMAC-SHA256 pick + landing angle — see common/wheel.py).
Options (segments + weights + multipliers + icon/colorHex) are
operator-configured in DearLive; the engine only resolves the committed
seed into an outcome. Betting/service integration stays planned until
business confirms rules (plugin status="planned" — not playable via
plugins.create).
"""
from common.engine import CommonGameEngine
from common.wheel import wheel_outcome

GAME_ID = "baby-king"


def spin(options, server_seed: str, client_seed: str, nonce: int) -> dict:
    """Pure outcome: weighted pick + angle. Deterministic per seed."""
    return wheel_outcome(options, server_seed, client_seed, nonce)


def _todo(*args, **kwargs):
    raise NotImplementedError("Baby King betting not developed yet — see roadmap")


class BabyKingEngine(CommonGameEngine):
    game_id = GAME_ID


for _m in ("createSession", "joinSession", "leaveSession", "getState",
           "validateAction", "applyAction", "startRound", "endRound",
           "calculateResult", "settle", "cancel", "handleTimeout",
           "handleReconnect"):
    setattr(BabyKingEngine, _m, staticmethod(_todo))

# Back-compat name (previous stub).
AnimalWheelEngine = BabyKingEngine
