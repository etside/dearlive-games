"""Game 3: Animal/Food Wheel — FUTURE MODULE. Interface stub only.

Shares common.CommonGameEngine + lifecycle + wallet + webhooks when built.
See BRD SRS Game 2 (Animal/Food Wheel) + docs/business-tbc.md for TBC list.
"""
from common.engine import CommonGameEngine


class AnimalWheelEngine(CommonGameEngine):
    game_id = "animal-food-wheel"

    def _todo(self):
        raise NotImplementedError("Game 3 (Animal Wheel) not developed yet — see roadmap")

    def __getattr__(self, name):
        if name in ("createSession", "joinSession", "leaveSession", "getState",
                    "validateAction", "applyAction", "startRound", "endRound",
                    "calculateResult", "settle", "cancel", "handleTimeout",
                    "handleReconnect"):
            return lambda *a, **k: self._todo()
        raise AttributeError(name)
