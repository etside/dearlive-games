"""Game 2: Greedy — FUTURE MODULE (registered plugin, status=planned).

Shares CommonGameEngine + lifecycle + wallet + webhooks when built.
See BRD SRS Game 1 (Greedy) + docs/business-tbc.md for its TBC list.
"""
from common.engine import CommonGameEngine


def _todo(*args, **kwargs):
    raise NotImplementedError("Game 2 (Greedy) not developed yet — see roadmap")


class GreedyEngine(CommonGameEngine):
    game_id = "greedy"


for _m in ("createSession", "joinSession", "leaveSession", "getState",
           "validateAction", "applyAction", "startRound", "endRound",
           "calculateResult", "settle", "cancel", "handleTimeout",
           "handleReconnect"):
    setattr(GreedyEngine, _m, staticmethod(_todo))
