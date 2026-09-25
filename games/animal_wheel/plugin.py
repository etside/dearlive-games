"""Baby King engine plugin registration (Game 3, LIVE).

Display name "Baby King" on the wheel slot (DearLive current wheel logic —
WheelDriver: weighted HMAC pick + landing angle — same as Food Wheel).
Full service implementation via shared WheelService — betting, rounds,
auto-bet, settlement, audit.
"""
from common import plugins
from common.plugins import engine_plugin
from games.wheel_common.service import WheelService


@engine_plugin("baby-king", "Baby King", "baby-king-1.0.0-tbc", status="live",
               tbc=("G2-BR-02", "G2-BR-03", "G2-BR-04", "G2-BR-05"),
               description="Baby King wheel betting (wheel slot). Full service: rounds, bets, auto-bet, settlement.",
               entry="/baby-king/", dearlive_code="food_wheel")
def make_room(*args, **kwargs):
    """Factory for WheelService — used by teen_patti_pro API handler."""
    room_id = args[0] if args else kwargs.get('room_id', 'default')
    config = kwargs.get('config')
    if config is None:
        from games.wheel_common.configs import baby_king_config
        config = baby_king_config()
    return WheelService(config=config)


# Back-compat aliases (previous stub ids + DearLive food-wheel code).
plugins.alias("animal-food-wheel", "baby-king")
plugins.alias("food-wheel", "baby-king")
plugins.alias("food_wheel", "baby-king")
