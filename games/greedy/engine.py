"""Game 2: Greedy — FUTURE MODULE. Interface stub only (not developed).

Shares common.CommonGameEngine + lifecycle + wallet + webhooks when built.
See BRD SRS Game 1 (Greedy) + docs/business-tbc.md for its TBC list.
"""
from common.engine import CommonGameEngine


class GreedyEngine(CommonGameEngine):
    game_id = "greedy"

    def _todo(self):
        raise NotImplementedError("Game 2 (Greedy) not developed yet — see roadmap")

    createSession = joinSession = leaveSession = getState = validateAction = None
    def __getattr__(self, name):
        if name in ("createSession", "joinSession", "leaveSession", "getState",
                    "validateAction", "applyAction", "startRound", "endRound",
                    "calculateResult", "settle", "cancel", "handleTimeout",
                    "handleReconnect"):
            return lambda *a, **k: self._todo()
        raise AttributeError(name)
