"""Admin control surface for Teen Patti Pro (game-agnostic pattern for 2/3).

Transport lives in games/teen_patti_pro/api.py (/api/v1/admin/*). This module
owns the RBAC matrix + privileged-action audit wrapper so every game reuses it.

Roles:
  superadmin: everything (config versions, API clients, launch-token policy,
              webhook destinations, permissions, bot policy, maintenance).
  admin:      live ops (rounds, rooms, players, bets, results, settlements,
              monitoring, reports, webhook status) + non-breaking config.
  viewer:     read-only (monitoring, reports, audit).

Every privileged call MUST pass through require() + audit.record().
Bots (auto-play) use the same place_bet path as players: same validation,
same settlement. No forceWin/forceLoss flags exist anywhere in this codebase
(by design — grep for them in review).
"""
from typing Callable, Dict


MATRIX: Dict[str, set] = {
    "config.read": {"superadmin", "admin", "viewer"},
    "config.write": {"superadmin"},
    "config.confirm": {"superadmin"},  # flipping confirmed=True (TBC sign-off)
    "rounds.control": {"superadmin", "admin"},  # start/close/result/settle/cancel
    "rooms.manage": {"superadmin", "admin"},
    "players.view": {"superadmin", "admin", "viewer"},
    "bets.view": {"superadmin", "admin", "viewer"},
    "results.view": {"superadmin", "admin", "viewer"},
    "settlements.view": {"superadmin", "admin", "viewer"},
    "webhooks.manage": {"superadmin"},
    "audit.view": {"superadmin", "admin", "viewer"},
    "maintenance": {"superadmin"},
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
