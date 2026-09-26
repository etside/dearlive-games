"""The scheduled-change activation sweep.

The sweep is what makes a scheduled change real. Storing the row proves
nothing; these tests pin the behaviour an operator is actually promised: it
applies when due, it applies in scheduled order, it records what it did, and
a change that fails is reported rather than quietly retried forever.
"""
import unittest

from common.admin_store import AdminStoreUnavailable
from common.config_scheduler import (CLAIM_GRACE_SECONDS, ScheduledChangeSweeper,
                                     apply_due_changes)

NOW = "2026-10-01T02:00:00+00:00"


def _change(change_id, target_type="profit_risk", target_id="default",
            payload=None, effective_at=NOW):
    return {"change_id": change_id, "target_type": target_type,
            "target_id": target_id, "payload": payload or {},
            "effective_at": effective_at, "status": "PENDING"}


class _Store:
    def __init__(self, due=None, **returns):
        self._due = list(due or [])
        self._r = returns
        self.claims = []
        self.applied = []
        self.failed = []
        self.claim_available = True

    def claim_due_scheduled_changes(self, now_iso, limit=25, grace_seconds=300):
        self.claims.append((now_iso, limit, grace_seconds))
        due, self._due = self._due, []
        return due

    def due_scheduled_changes(self, now_iso, limit=25):
        due, self._due = self._due, []
        return due

    def mark_scheduled_applied(self, change_id, result=None):
        self.applied.append((change_id, result))
        return True

    def mark_scheduled_failed(self, change_id, reason):
        self.failed.append((change_id, reason))
        return True

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*a, **kw):
            value = self._r.get(name)
            if callable(value):
                return value(*a, **kw)
            return value
        return _call


class ApplyDueChangesTest(unittest.TestCase):
    def test_nothing_due_is_a_no_op(self):
        store = _Store(due=[])
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["checked"], 0)
        self.assertEqual(report["applied"], [])
        self.assertEqual(store.applied, [])

    def test_applies_a_due_profit_risk_change(self):
        store = _Store(due=[_change("c1")],
                       save_profit_risk=lambda payload, actor: {
                           "config_id": payload.get("config_id", "default"),
                           "version": 4})
        report = apply_due_changes(store, now=NOW)
        self.assertEqual([a["change_id"] for a in report["applied"]], ["c1"])
        self.assertEqual(report["failed"], [])
        self.assertEqual(store.applied[0][0], "c1")

    def test_a_failing_change_is_recorded_and_never_silently_retried(self):
        store = _Store(due=[_change("bad")],
                       save_profit_risk=_boom("ValueError", "house_edge out of range"))
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["applied"], [])
        self.assertEqual(len(report["failed"]), 1)
        self.assertIn("house_edge out of range", report["failed"][0]["reason"])
        self.assertEqual(store.failed[0][0], "bad")
        self.assertEqual(store.applied, [], "a failed change must not be marked applied")

    def test_one_bad_change_does_not_stop_the_others(self):
        # This is the whole reason apply_due_changes returns a report instead of
        # raising: a single malformed row must not block every later change.
        store = _Store(due=[_change("bad"), _change("good")],
                       save_profit_risk=_boom_then_ok())
        report = apply_due_changes(store, now=NOW)
        self.assertEqual([a["change_id"] for a in report["applied"]], ["good"])
        self.assertEqual([f["change_id"] for f in report["failed"]], ["bad"])

    def test_backlog_is_applied_in_the_order_the_store_returns(self):
        # Oldest-first ordering is the store's guarantee (its SQL has
        # ORDER BY effective_at). The sweeper's job is to preserve that order
        # rather than re-sorting: a panel that scheduled "set edge to 6%" then
        # "set edge to 5%" must get 5% at the end.
        store = _Store(due=[_change("first"), _change("second"), _change("third")],
                       save_profit_risk=lambda p, a: {"version": 1})
        report = apply_due_changes(store, now=NOW)
        self.assertEqual([a["change_id"] for a in report["applied"]],
                         ["first", "second", "third"])

    def test_uses_the_claiming_read_with_a_grace_window(self):
        store = _Store(due=[])
        apply_due_changes(store, now=NOW)
        self.assertEqual(store.claims, [(NOW, 25, CLAIM_GRACE_SECONDS)])

    def test_falls_back_when_the_store_cannot_claim(self):
        store = _Store(due=[_change("c1")], save_profit_risk=lambda p, a: {"v": 1})
        store.claim_available = False
        # Hide the claim method the way an older store would.
        store.__dict__["claim_due_scheduled_changes"] = None
        report = apply_due_changes(store, now=NOW)
        self.assertEqual([a["change_id"] for a in report["applied"]], ["c1"])

    # -- target types ------------------------------------------------------

    def test_settings_change_lands(self):
        store = _Store(due=[_change("c1", "settings", "")],
                       put_settings=lambda values, actor="": {"stored": len(values)})
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["applied"][0]["target_type"], "settings")

    def test_game_config_change_needs_an_enabled_field(self):
        store = _Store(due=[_change("c1", "game_config", "teen-patti-pro")])
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["failed"][0]["change_id"], "c1")
        self.assertIn("enabled", report["failed"][0]["reason"])

    def test_game_config_change_can_disable_a_game(self):
        store = _Store(due=[_change("c1", "game_config", "teen-patti-pro",
                                    {"enabled": False})],
                       set_game_enabled=lambda gid, en, **kw: {
                           "game_id": gid, "enabled": en})
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["applied"][0]["change_id"], "c1")

    def test_package_change_without_a_target_creates_one(self):
        store = _Store(due=[_change("c1", "package", "", {"name": "New",
                                                           "coins": 100,
                                                           "price_minor": 99})],
                       create_package=lambda **kw: kw)
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["applied"][0]["change_id"], "c1")

    def test_package_change_with_a_target_updates_it(self):
        store = _Store(due=[_change("c1", "package", "pkg-1", {"coins": 750})],
                       update_package=lambda pid, **kw: dict(kw, package_id=pid))
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["applied"][0]["change_id"], "c1")

    def test_unknown_target_type_is_reported_not_retried(self):
        store = _Store(due=[_change("c1", "from_the_future", "x")])
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["applied"], [])
        self.assertEqual(len(report["skipped"]), 1)
        self.assertEqual(store.failed[0][0], "c1")

    # -- resilience --------------------------------------------------------

    def test_no_database_reports_an_error_and_does_not_raise(self):
        # The sweeper runs on a timer inside the API process. If it raised, the
        # timer thread would die and every later change would silently never
        # apply.
        store = _Store()
        store.__dict__["claim_due_scheduled_changes"] = _raise_unavailable
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["error"], "AdminStoreUnavailable")
        self.assertEqual(report["checked"], 0)

    def test_apply_error_still_reports_the_bookkeeping_failure(self):
        # The change is in effect but the row was not recorded. Saying so beats
        # reporting success: an operator would otherwise assume it is tracked.
        store = _Store(due=[_change("c1")],
                       save_profit_risk=lambda p, a: {"version": 4})
        store.__dict__["mark_scheduled_applied"] = _raise_runtime
        report = apply_due_changes(store, now=NOW)
        self.assertEqual(report["applied"], [])
        self.assertIn("could not be recorded", report["failed"][0]["reason"])

    def test_result_recorded_is_kept_json_safe(self):
        store = _Store(due=[_change("c1")],
                       save_profit_risk=lambda p, a: {
                           "version": 4, "versions": [{"nested": True}]})
        apply_due_changes(store, now=NOW)
        result = store.applied[0][1]["result"]
        self.assertEqual(result, {"version": 4},
                         "non-scalar fields must be trimmed, not stored raw")


class SweeperLifecycleTest(unittest.TestCase):
    def test_start_and_stop_are_both_safe_to_call_twice(self):
        store = _Store(due=[])
        sweeper = ScheduledChangeSweeper(store, interval_seconds=3600)
        sweeper.start().start()
        sweeper.stop().stop()
        self.assertIsNone(sweeper._thread)

    def test_interval_is_floored(self):
        # A zero or negative interval would spin the loop and hammer Postgres.
        self.assertEqual(ScheduledChangeSweeper(_Store(), 0).interval, 5)
        self.assertEqual(ScheduledChangeSweeper(_Store(), -30).interval, 5)


def _boom(exc_name, message):
    def _raise(*a, **kw):
        raise RuntimeError(message)
    _raise.__name__ = exc_name
    return _raise


def _boom_then_ok():
    state = {"first": True}

    def _call(payload, actor):
        if state["first"]:
            state["first"] = False
            raise ValueError("bad payload")
        return {"version": 1}
    return _call


def _raise_unavailable(*a, **kw):
    raise AdminStoreUnavailable("DATABASE_URL is not configured")


def _raise_runtime(*a, **kw):
    raise RuntimeError("connection reset")


if __name__ == "__main__":
    unittest.main()
