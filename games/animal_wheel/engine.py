"""Game 3: Baby King — wheel outcome engine (DearLive wheel slot).

Same deterministic logic as DearLive current wheel games (`WheelDriver`:
weighted HMAC-SHA256 pick + landing angle — see common/wheel.py).
Options (segments + weights + multipliers + icon/colorHex) are
operator-configured in DearLive; the engine only resolves the committed
seed into an outcome.
"""
from common.engine import CommonGameEngine
from common.wheel import wheel_outcome

GAME_ID = "baby-king"


def spin(options, server_seed: str, client_seed: str, nonce: int) -> dict:
    """Pure outcome: weighted pick + angle. Deterministic per seed."""
    return wheel_outcome(options, server_seed, client_seed, nonce)


class BabyKingEngine(CommonGameEngine):
    game_id = GAME_ID


# Back-compat name (previous stub).
AnimalWheelEngine = BabyKingEngine
