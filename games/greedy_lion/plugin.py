"""Greedy Lion engine plugin registration (LIVE).

Authoritative play: generic WheelService wired in teen_patti_pro/api.py
(rounds, server timer, bets, idempotent settlement, history, reconnect).
Outcome math: .engine.spin (parity with Uradhura WheelDriver greedy_lion).
"""
from common import plugins
from common.plugins import engine_plugin
from games.wheel_common.service import WheelService


@engine_plugin("greedy-lion", "Greedy Lion", "greedy-lion-1.0.0-tbc", status="live",
               tbc=("G1-BR-03", "G1-BR-04", "G1-BR-05"),
               description="Lion wheel betting. Full service via WheelService.",
               entry="/greedy-lion/", dearlive_code="greedy_lion")
def make_room(*args, **kwargs):
    """Factory for WheelService — used by teen_patti_pro API handler."""
    room_id = args[0] if args else kwargs.get('room_id', 'default')
    config = kwargs.get('config')
    if config is None:
        from games.wheel_common.configs import greedy_lion_config
        config = greedy_lion_config()
    return WheelService(config=config)


# DearLive-code alias.
plugins.alias("greedy_lion", "greedy-lion")
