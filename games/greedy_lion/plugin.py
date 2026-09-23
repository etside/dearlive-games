"""Greedy Lion engine plugin registration.

Authoritative play: generic WheelService wired in teen_patti_pro/api.py
(rounds, server timer, bets, idempotent settlement, history, reconnect).
Outcome math: .engine.spin (parity with Uradhura WheelDriver greedy_lion).
"""
from common import plugins
from common.plugins import engine_plugin


@engine_plugin("greedy-lion", "Greedy Lion", "greedy-lion-1.0.0-tbc", status="planned",
               tbc=("G1-BR-03", "G1-BR-04", "G1-BR-05"),
               description="Lion wheel betting. Outcome engine ready; playable via WheelService.",
               entry="/greedy-lion/", dearlive_code="greedy_lion")
def make_room(*args, **kwargs):
    from .engine import GreedyLionEngine  # noqa
    raise NotImplementedError("Greedy Lion plays via WheelService, not plugins.create")


# DearLive-code alias.
plugins.alias("greedy_lion", "greedy-lion")
