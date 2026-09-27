"""The scheduled-change sweeper must survive a store that returns None.

Regression, seen live on the VPS: every 60 seconds the config sweeper logged

    File "common/config_scheduler.py", line 112, in apply_due_changes
        report["checked"] = len(due)
    TypeError: object of type 'NoneType' has no len()

Root cause. `AdminStore.claim_due_scheduled_changes` is declared as a protocol
stub whose body is `...`, so it evaluates to None. `PostgresAdminStore`
subclasses `AdminStore` but never overrides it, so `getattr(store,
"claim_due_scheduled_changes", None)` finds the *inherited stub*. The stub is
callable, so the scheduler called it, got None, and passed that to `len()`.

A stub whose body is `...` looks callable and returns None -- the single most
dangerous shape a protocol method can have, because both `getattr(..., None)`
and `callable()` report it as usable.

These tests pin both halves of the fix: the inherited stub must be detected and
ignored, and a None from any path must not reach `len()`.
"""
import unittest

from common.admin_store import AdminStore, PostgresAdminStore
from common.config_scheduler import apply_due_changes


class _StoreWithNothingDue(AdminStore):
    """A store that implements the plain read but not the claiming read.

    Mirrors PostgresAdminStore's real situation: it inherits the `...` stub.
    """

    def __init__(self):
        self.calls = []

    def due_scheduled_changes(self, now_iso, limit=25):
        self.calls.append("due_scheduled_changes")
        return []


class _StoreClaimReturnsNone(AdminStore):
    def due_scheduled_changes(self, now_iso, limit=25):
        return []

    def claim_due_scheduled_changes(self, now_iso, limit=25, grace_seconds=300):
        return None


class _StoreClaimRaises(AdminStore):
    def due_scheduled_changes(self, now_iso, limit=25):
        return []

    def claim_due_scheduled_changes(self, now_iso, limit=25, grace_seconds=300):
        raise RuntimeError("claim unavailable")


class ConfigSchedulerNoneSafetyTest(unittest.TestCase):
    def test_inherited_stub_is_not_treated_as_implemented(self):
        """The production failure: PostgresAdminStore inherits the stub."""
        store = _StoreWithNothingDue()
        # Sanity: the stub really is inherited and really is callable.
        self.assertTrue(callable(store.claim_due_scheduled_changes))
        self.assertIs(store.claim_due_scheduled_changes.__func__,
                      AdminStore.claim_due_scheduled_changes)
        report = apply_due_changes(store)
        self.assertNotIn("error", report, report)
        self.assertEqual(report.get("checked"), 0)
        self.assertEqual(store.calls, ["due_scheduled_changes"],
                         "must fall back to the plain read, not the stub")

    def test_postgres_store_shape_is_detected(self):
        """Guard the real class, not just the test double."""
        # Accessing on the class yields the plain function, so compare directly
        # (on an *instance* it would be a bound method and need __func__).
        self.assertIs(PostgresAdminStore.claim_due_scheduled_changes,
                      AdminStore.claim_due_scheduled_changes,
                      "PostgresAdminStore still inherits the None-returning "
                      "stub; implement it or keep the fallback honest")
        self.assertIsNone(PostgresAdminStore.claim_due_scheduled_changes(
            PostgresAdminStore.__new__(PostgresAdminStore), "now"),
            "the stub returns None, which is what crashed the sweeper")

    def test_none_from_a_real_claim_does_not_crash(self):
        store = _StoreClaimReturnsNone()
        report = apply_due_changes(store)
        self.assertNotIn("error", report, report)
        self.assertEqual(report.get("checked"), 0)

    def test_raising_claim_is_contained(self):
        store = _StoreClaimRaises()
        report = apply_due_changes(store)
        self.assertEqual(report.get("error"), "RuntimeError")

    def test_sweep_never_raises_for_any_store(self):
        for store in (_StoreWithNothingDue(), _StoreClaimReturnsNone(),
                      _StoreClaimRaises()):
            with self.subTest(store=type(store).__name__):
                try:
                    apply_due_changes(store)
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"sweeper raised {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    unittest.main()
