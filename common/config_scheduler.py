"""Applies scheduled config changes once they come due.

A scheduled change is a promise made to an operator: "at 02:00 UTC the house
edge becomes 6%". Storing the row is only half of that promise -- something has
to notice the clock and actually make the change, or the panel would show a
change as PENDING forever while the engine kept running the old numbers.

Design notes:

* The sweep is idempotent and safe to run from several processes at once. It
  claims rows by flipping PENDING -> APPLYING with a conditional UPDATE, so
  only the process whose UPDATE affected a row goes on to apply it.
* A change that fails is marked FAILED with the reason and is never retried
  automatically. A config change that failed once (bad payload, constraint
  violation) will fail identically forever, and silently retrying it every
  sweep would fill the audit log while an operator believed it was applied.
* Applying a change is a single transaction, so a crash mid-apply leaves the
  row APPLYING rather than a half-written config. APPLYING is treated as
  abandoned by the claim query only after a grace period, so a row cannot be
  stranded forever by a crash.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("dearlive.scheduler")

# How long a row may sit in APPLYING before another process may reclaim it.
CLAIM_GRACE_SECONDS = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _handlers(store) -> Dict[str, Callable[[str, Dict[str, Any]], Any]]:
    """Map a scheduled target_type onto the store call that performs it.

    Built per call rather than at import so a test can swap the store. Each
    handler returns whatever the store returns; the sweeper only cares that
    it did not raise.
    """

    def _profit_risk(target_id: str, payload: Dict[str, Any]):
        payload = dict(payload or {})
        actor = payload.pop("created_by", None) or payload.pop("actor", "") or "scheduler"
        return store.save_profit_risk(payload, actor)

    def _settings(target_id: str, payload: Dict[str, Any]):
        payload = dict(payload or {})
        actor = payload.pop("created_by", None) or payload.pop("actor", "") or "scheduler"
        return store.put_settings(payload, actor=actor)

    def _package(target_id: str, payload: Dict[str, Any]):
        payload = dict(payload or {})
        # An empty target_id means "this is a new package"; otherwise update the
        # named one. The panel sends the id it was editing.
        package_id = payload.pop("package_id", "")
        if not target_id:
            return store.create_package(package_id=package_id or "", **payload)
        return store.update_package(target_id, **payload)

    def _game_config(target_id: str, payload: Dict[str, Any]):
        payload = dict(payload or {})
        enabled = payload.pop("enabled", None)
        if enabled is None:
            raise ValueError("game_config change needs an 'enabled' field")
        return store.set_game_enabled(
            target_id, bool(enabled), status=payload.pop("status", None),
            message=str(payload.pop("message", "")))

    return {
        "profit_risk": _profit_risk,
        "settings": _settings,
        "package": _package,
        "game_config": _game_config,
    }


def apply_due_changes(store, now: Optional[str] = None,
                      limit: int = 25) -> Dict[str, Any]:
    """Apply every PENDING change whose effective_at has passed.

    Returns a report rather than raising: a failing change is data about the
    failure, and a sweeper that dies on the first bad row stops applying every
    later, perfectly good one.
    """
    now = now or _now_iso()
    handlers = _handlers(store)
    report: Dict[str, Any] = {"now": now, "checked": 0, "applied": [],
                              "failed": [], "skipped": []}

    # Prefer the claiming read when the store has it, so two API processes
    # sweeping at the same instant cannot both apply the same change. A store
    # without it (a test double, or an older deploy) still works: worst case
    # both processes see the same row.
    claim = getattr(store, "claim_due_scheduled_changes", None)
    try:
        due = (claim(now, limit=limit, grace_seconds=CLAIM_GRACE_SECONDS)
               if callable(claim)
               else store.due_scheduled_changes(now, limit=limit))
    except Exception as exc:
        # No database, or the migration is not applied. The sweeper is a
        # convenience loop; it must never take the server down with it.
        log.warning("scheduled change sweep skipped: %s", type(exc).__name__)
        report["error"] = type(exc).__name__
        return report

    report["checked"] = len(due)
    for change in due:
        change_id = str(change.get("change_id") or "")
        target_type = str(change.get("target_type") or "")
        target_id = str(change.get("target_id") or "")
        handler = handlers.get(target_type)
        if handler is None:
            # The schema constrains target_type, so this only happens if a row
            # predates a migration. Record it rather than retrying forever.
            report["skipped"].append({"change_id": change_id,
                                      "reason": f"unknown target_type {target_type!r}"})
            _mark_failed(store, change_id, f"unknown target_type {target_type!r}")
            continue
        try:
            result = handler(target_id, change.get("payload") or {})
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:400]
            report["failed"].append({"change_id": change_id, "reason": reason})
            _mark_failed(store, change_id, reason)
            continue
        try:
            store.mark_scheduled_applied(change_id, {"result": _brief(result)})
        except Exception as exc:
            # The change is in effect but we could not record that. Say so --
            # silently reporting success would hide a bookkeeping problem.
            report["failed"].append({
                "change_id": change_id,
                "reason": f"applied but could not be recorded: {type(exc).__name__}"})
            continue
        report["applied"].append({
            "change_id": change_id, "target_type": target_type,
            "target_id": target_id})
        log.info("applied scheduled change %s (%s/%s)", change_id,
                 target_type, target_id or "-")
    return report


def _mark_failed(store, change_id: str, reason: str) -> None:
    try:
        store.mark_scheduled_failed(change_id, reason)
    except Exception:
        log.warning("could not mark change %s failed", change_id)


def _brief(result: Any) -> Any:
    """Trim a store return value to something safe to store as JSON."""
    if isinstance(result, dict):
        return {k: v for k, v in result.items()
                if isinstance(v, (str, int, float, bool, type(None)))}
    if isinstance(result, list):
        return f"{len(result)} rows"
    return str(result)[:200]


class ScheduledChangeSweeper:
    """Background loop that calls apply_due_changes on an interval.

    Deliberately not a daemon thread created at import time: the API process
    and the staging adapter both import this module, and a thread started on
    import would try to reach Postgres from a worker that was never asked for
    one. Call start() explicitly from the entrypoint.
    """

    def __init__(self, store, interval_seconds: int = 60):
        self.store = store
        self.interval = max(5, int(interval_seconds))
        self._stop = None
        self._thread = None

    def start(self):
        import threading
        if self._thread is not None:
            return self
        self._stop = threading.Event()

        def _loop():
            while not self._stop.wait(self.interval):
                try:
                    apply_due_changes(self.store)
                except Exception:
                    log.exception("scheduled change sweep failed")

        self._thread = threading.Thread(target=_loop, name="sched-sweeper",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0):
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        return self
