"""Skill/hook system — optional cross-cutting modules on engine events.

Skills observe (audit log, webhook fan-out, odds display feeds, JEV QA sampling);
they NEVER decide money. Hard guarantees:
  1. A skill exception can never break/abort the money path (isolated, audited).
  2. Skills run AFTER the authoritative state transition commits.
  3. Skills are admin-toggleable per game (enabled set); unknown skills rejected.
  4. Skills receive copies, never live state objects.

Hook points: on_bet, on_close, on_result, on_settle, on_cancel, on_round_start.
"""
from typing import Callable, Dict, List


HOOKS = ("on_round_start", "on_bet", "on_close", "on_result", "on_settle", "on_cancel")


class Skill:
    skill_id: str = ""
    description: str = ""

    def handles(self) -> List[str]:
        return []

    def run(self, hook: str, payload: dict) -> None:
        raise NotImplementedError


class SkillBus:
    def __init__(self, audit_log=None):
        self._skills: Dict[str, Skill] = {}
        self._enabled: Dict[str, set] = {}  # game_id -> skill_ids
        self._audit = audit_log

    def register(self, skill: Skill):
        if not skill.skill_id or skill.skill_id in self._skills:
            raise ValueError(f"bad/duplicate skill: {skill.skill_id!r}")
        for h in skill.handles():
            if h not in HOOKS:
                raise ValueError(f"skill {skill.skill_id} unknown hook {h!r}")
        self._skills[skill.skill_id] = skill

    def enable(self, game_id: str, skill_id: str):
        if skill_id not in self._skills:
            raise KeyError(f"unknown skill {skill_id!r}")
        self._enabled.setdefault(game_id, set()).add(skill_id)

    def disable(self, game_id: str, skill_id: str):
        self._enabled.get(game_id, set()).discard(skill_id)

    def emit(self, game_id: str, hook: str, payload: dict) -> List[dict]:
        """Fire-and-isolate: returns per-skill outcomes; failures recorded."""
        outcomes = []
        for sid in sorted(self._enabled.get(game_id, set())):
            skill = self._skills.get(sid)
            if skill is None or hook not in skill.handles():
                continue
            try:
                skill.run(hook, dict(payload))
                outcomes.append({"skill": sid, "ok": True})
            except Exception as exc:  # isolate: money path already committed
                outcomes.append({"skill": sid, "ok": False, "error": str(exc)})
                if self._audit is not None:
                    self._audit.record("system", "skill.failed", "skill", sid,
                                       after={"hook": hook, "error": str(exc)})
        return outcomes

    def catalog(self) -> List[dict]:
        return [{"skill_id": s.skill_id, "description": s.description,
                 "hooks": s.handles()} for s in self._skills.values()]


class WebhookFanoutSkill(Skill):
    """Bundled skill: forwards engine events to webhook delivery log."""

    skill_id = "webhook-fanout"
    description = "Fan out engine events to signed webhook delivery queue."

    def __init__(self, fire: Callable):
        self._fire = fire

    def handles(self):
        return ["on_round_start", "on_bet", "on_close", "on_result", "on_settle", "on_cancel"]

    def run(self, hook: str, payload: dict) -> None:
        kinds = {"on_round_start": "round.started", "on_bet": "bet.accepted",
                 "on_close": "betting.closed", "on_result": "result.published",
                 "on_settle": "settlement.completed", "on_cancel": "round.cancelled"}
        self._fire(kinds[hook], payload)
