"""Greedy Monkey engine plugin registration (Game 2, LIVE).

DearLive current name "Greedy Monkey" (Uradhura internalCode `greedy_monkey`,
WheelDriver: weighted HMAC pick + landing angle). Full service implementation
via shared WheelService — betting, rounds, auto-bet, settlement, audit.
"""
from common import plugins
from common.plugins import engine_plugin
from games.wheel_common.service import WheelService


@engine_plugin("greedy-monkey", "Greedy Monkey", "greedy-monkey-1.0.0-tbc", status="live",
               tbc=("G1-BR-03", "G1-BR-04", "G1-BR-05"),
               description="Monkey wheel betting (BRD Game 1). Full service: rounds, bets, auto-bet, settlement.",
               entry="/greedy-monkey/", dearlive_code="greedy_monkey")
def make_room(*args, **kwargs):
    """Factory for WheelService — used by teen_patti_pro API handler."""
    room_id = args[0] if args else kwargs.get('room_id', 'default')
    config = kwargs.get('config')
    if config is None:
        from games.wheel_common.configs import greedy_config
        config = greedy_config()
    return WheelService(config=config)


# Back-compat + DearLive-code aliases.
plugins.alias("greedy", "greedy-monkey")
plugins.alias("greedy_monkey", "greedy-monkey")
# Delivery name for the Monkey Wheel plugin (same authoritative engine).
plugins.alias("monkey-wheel", "greedy-monkey")
plugins.alias("monkey_wheel", "greedy-monkey")
