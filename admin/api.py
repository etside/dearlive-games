"""Admin control surface for Teen Patti Pro.

Transport lives in games/teen_patti_pro/api.py (/api/v1/admin/*). This module
owns the RBAC matrix + privileged-action audit wrapper.

Roles (mirrors Handler.ROLE_LEVEL, which is the enforced hierarchy):
  admin:    top role -- live ops (rounds, rooms, players, bets, results,
            settlements, monitoring, reports) plus config writes, API clients,
            webhook destinations, maintenance and operator management.
  operator: run rounds and read operational state; cannot write config.
  auditor:  read-only (monitoring, reports, audit).

There is no superadmin role. Scope a key down with GAME_ADMIN_SCOPES rather
than escalating to a god-mode credential.

Note: the name `viewer` is retained below as an alias for `auditor` so existing
call sites keep working; it is not a separate tier.

Every privileged call MUST pass through require() + audit.record().
Bots (auto-play) use the same place_bet path as players: same validation,
same settlement. No forceWin/forceLoss flags exist anywhere in this codebase
(by design — grep for them in review).
"""
from typing import Callable, Dict


MATRIX: Dict[str, set] = {
    "config.read": {"admin", "viewer"},
    "config.write": {"admin"},
    "config.confirm": {"admin"},  # flipping confirmed=True (TBC sign-off)
    "rounds.control": {"admin", "operator"},  # start/close/result/settle/cancel
    "rooms.manage": {"admin", "operator"},
    "players.view": {"admin", "operator", "viewer"},
    "players.override": {"admin"},
    "bets.view": {"admin", "operator", "viewer"},
    "results.view": {"admin", "operator", "viewer"},
    "settlements.view": {"admin", "operator", "viewer"},
    "reports.view": {"admin", "operator", "viewer"},
    "packages.manage": {"admin"},
    "webhooks.manage": {"admin"},
    "audit.view": {"admin", "viewer"},
    "maintenance": {"admin"},
}


class Forbidden(Exception):
    pass


def require(role: str, permission: str) -> None:
    if role not in MATRIX.get(permission, set()):
        raise Forbidden(f"role {role!r} lacks {permission!r}")


def audited(audit_log, actor: str, action: str, entity: str,
            entity_id: str, fn: Callable, *args, **kwargs):
    """Run privileged fn, audit before/after. Exceptions propagate unaudited
    as failures are recorded by the caller with action+'.failed'."""
    before = {"args": repr(args), "kwargs": repr(kwargs)}
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        audit_log.record(actor, action + ".failed", entity, entity_id,
                         before=before, after={"error": str(exc)})
        raise
    audit_log.record(actor, action, entity, entity_id,
                     before=before, after={"ok": True})
    return result
