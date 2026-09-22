"""Game 3: Animal/Food Wheel — FUTURE MODULE (registered plugin, status=planned).

Shares CommonGameEngine + lifecycle + wallet + webhooks when built.
See BRD SRS Game 2 (Animal/Food Wheel) + docs/business-tbc.md for TBC list.
"""
from common.engine import CommonGameEngine


def _todo(*args, **kwargs):
    raise NotImplementedError("Game 3 (Animal Wheel) not developed yet — see roadmap")


class AnimalWheelEngine(CommonGameEngine):
    game_id = "animal-food-wheel"


for _m in ("createSession", "joinSession", "leaveSession", "getState",
           "validateAction", "applyAction", "startRound", "endRound",
           "calculateResult", "settle", "cancel", "handleTimeout",
           "handleReconnect"):
    setattr(AnimalWheelEngine, _m, staticmethod(_todo))
