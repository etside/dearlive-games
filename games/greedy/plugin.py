"""Greedy Monkey engine plugin registration (Game 2, planned stub).

DearLive current name "Greedy Monkey" (Uradhura internalCode `greedy_monkey`,
WheelDriver: weighted HMAC pick + landing angle). Deterministic outcome logic
lives in .engine (parity); betting/service integration stays planned until
business confirms rules.
"""
from common import plugins
from common.plugins import engine_plugin


@engine_plugin("greedy-monkey", "Greedy Monkey", "0.1.0", status="planned",
               tbc=("G1-BR-03", "G1-BR-04", "G1-BR-05"),
               description="Monkey wheel betting (BRD Game 1). Outcome engine ready; not playable yet.",
               entry="/greedy-monkey/", dearlive_code="greedy_monkey")
def make_room(*args, **kwargs):
    from .engine import GreedyMonkeyEngine  # noqa
    raise NotImplementedError("Greedy Monkey not developed yet")


# Back-compat + DearLive-code aliases.
plugins.alias("greedy", "greedy-monkey")
plugins.alias("greedy_monkey", "greedy-monkey")
