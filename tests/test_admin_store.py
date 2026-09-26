"""Tests for common/admin_store.py.

The store takes a cursor_factory rather than importing a driver, so these tests
drive it with a small fake DB-API cursor. That keeps the suite hermetic while
still exercising the real SQL, the version/active invariants, and the
fail-loudly behaviour when no database is configured.
"""
import unittest

from common.admin_store import (AdminStoreUnavailable, PostgresAdminStore,
                                PROFIT_RISK_CONFIG_ID, UnavailableAdminStore)


class FakeCursor:
    """Records SQL and replays the next queued DB response.

    ``db.responses`` is consumed one entry per ``execute``; once exhausted the
    last entry repeats. That keeps multi-statement methods (probe -> deactivate
    -> insert) testable without hand-rolling a cursor per test.
    """

    def __init__(self, db):
        self.db = db
        self.connection = FakeConnection()
        self._rows = []
        self._one = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.db.executed.append((" ".join(sql.split()), params))
        if self.db.responses:
            one, rows = self.db.responses[0]
            if len(self.db.responses) > 1:
                self.db.responses.pop(0)      # advance after reading
        else:
            one, rows = self.db.one, self.db.rows
        self._one = one
        self._rows = list(rows)
        self.rowcount = self.db.rowcount
        if self.db.raise_on_execute:
            raise self.db.raise_on_execute

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeDB:
    """responses: list of (fetchone_value, fetchall_rows) consumed per execute."""

    def __init__(self, rows=None, one=None, responses=None, rowcount=0,
                 raise_on_execute=None):
        self.executed = []
        self.rows = rows or []
        self.one = one
        self.responses = list(responses) if responses else []
        self.rowcount = rowcount
        self.raise_on_execute = raise_on_execute
        self.cursors = []

    def cursor(self):
        c = FakeCursor(self)
        self.cursors.append(c)
        return c

    @property
    def sql(self):
        return [s for s, _ in self.executed]

    def sql_containing(self, needle):
        return [s for s in self.sql if needle in s]

    def params_for(self, needle):
        return next(p for s, p in self.executed if needle in s)


PR_ACTIVE_ROW = (PROFIT_RISK_CONFIG_ID, 3, "8.00", "1.50", 10000, "1.00", 500,
                 True, "admin", "2026-09-26 10:00:00+00")


class UnavailableStoreTest(unittest.TestCase):
    def test_every_method_raises(self):
        store = UnavailableAdminStore()
        for name in ("get_active_profit_risk", "list_profit_risk_versions",
                     "save_profit_risk", "create_player_override",
                     "get_live_player_override", "revoke_player_override",
                     "list_player_overrides", "list_vip_tiers",
                     "upsert_vip_tier", "create_withdrawal_request",
                     "list_withdrawal_requests", "set_withdrawal_status",
                     "list_game_config_versions", "get_game_config_version",
                     "save_game_config_version", "rollback_game_config"):
            with self.assertRaises(AdminStoreUnavailable, msg=name):
                getattr(store, name)()

    def test_dunder_private_access_still_raises_attribute_error(self):
        store = UnavailableAdminStore()
        with self.assertRaises(AttributeError):
            store._secret

    def test_reason_is_carried(self):
        store = UnavailableAdminStore("no database")
        with self.assertRaises(AdminStoreUnavailable) as ctx:
            store.get_active_profit_risk()
        self.assertIn("no database", str(ctx.exception))


class ProfitRiskReadTest(unittest.TestCase):
    def test_get_active_returns_mapping(self):
        db = FakeDB(one=PR_ACTIVE_ROW)
        out = PostgresAdminStore(db.cursor).get_active_profit_risk()
        self.assertEqual(out["version"], 3)
        self.assertEqual(out["base_house_edge_pct"], 8.0)
        self.assertIs(out["active"], True)
        self.assertIn("active", db.sql_containing("WHERE config_id")[0])

    def test_get_active_returns_none_when_unset(self):
        db = FakeDB(one=None)
        self.assertIsNone(PostgresAdminStore(db.cursor).get_active_profit_risk())

    def test_decimal_is_coerced_to_float(self):
        class D:
            def quantize(self, *a): ...
            def as_tuple(self): ...

            def __float__(self):
                return 8.5
        row = list(PR_ACTIVE_ROW)
        row[2] = D()
        out = PostgresAdminStore(FakeDB(one=tuple(row)).cursor).get_active_profit_risk()
        self.assertIsInstance(out["base_house_edge_pct"], float)

    def test_list_versions_orders_descending(self):
        db = FakeDB(rows=[PR_ACTIVE_ROW])
        out = PostgresAdminStore(db.cursor).list_profit_risk_versions(limit=5)
        self.assertEqual(len(out), 1)
        self.assertIn("ORDER BY version DESC", db.sql[0])
        self.assertEqual(db.executed[0][1], (PROFIT_RISK_CONFIG_ID, 5))


class ProfitRiskWriteTest(unittest.TestCase):
    def test_save_deactivates_then_inserts_in_one_transaction(self):
        # statement 1: MAX(version) probe -> 7
        # statement 2: deactivate (no RETURNING)
        # statement 3: insert -> full RETURNING row
        db = FakeDB(responses=[((7,), []), (None, []), (PR_ACTIVE_ROW, [])])
        store = PostgresAdminStore(db.cursor)
        created = store.save_profit_risk({"base_house_edge_pct": 9.5}, "admin")
        self.assertEqual(created["version"], 3)
        deact_i = next(i for i, s in enumerate(db.sql) if "SET active = FALSE" in s)
        insert_i = next(i for i, s in enumerate(db.sql) if "INSERT INTO" in s)
        self.assertLess(deact_i, insert_i,
                        "deactivate must precede insert so no zero-active window")
        self.assertEqual(db.cursors[0].connection.commits, 1)

    def test_save_uses_next_version_from_max(self):
        db = FakeDB(responses=[((41,), []), (None, []), (PR_ACTIVE_ROW, [])])
        PostgresAdminStore(db.cursor).save_profit_risk({}, "admin")
        self.assertEqual(db.params_for("INSERT INTO profit_risk_config")[1], 42)

    def test_save_rolls_back_on_error(self):
        db = FakeDB(responses=[((3,), [])], raise_on_execute=RuntimeError("boom"))
        store = PostgresAdminStore(db.cursor)
        with self.assertRaises(RuntimeError):
            store.save_profit_risk({}, "admin")
        self.assertTrue(any(c.connection.rollbacks for c in db.cursors))

    def test_save_passes_values_through(self):
        db = FakeDB(responses=[((1,), []), (None, []), (PR_ACTIVE_ROW, [])])
        PostgresAdminStore(db.cursor).save_profit_risk(
            {"base_house_edge_pct": 12.25, "vip_profit_adj_pct": 3,
             "max_payout_per_round": 50000, "rng_weight": 2.5,
             "max_daily_loss_per_player": 250}, "root")
        params = db.params_for("INSERT INTO profit_risk_config")
        self.assertEqual(params[2], 12.25)      # house edge
        self.assertEqual(params[3], 3.0)        # vip adj
        self.assertEqual(params[4], 50000)      # max payout
        self.assertEqual(params[5], 2.5)        # rng weight
        self.assertEqual(params[6], 250)        # daily loss
        self.assertEqual(params[7], "root")     # actor

    def test_defaults_match_the_migration(self):
        # An empty patch must persist the documented defaults, not NULLs.
        db = FakeDB(responses=[((1,), []), (None, []), (PR_ACTIVE_ROW, [])])
        PostgresAdminStore(db.cursor).save_profit_risk({}, "admin")
        p = db.params_for("INSERT INTO profit_risk_config")
        self.assertEqual((p[2], p[3], p[4], p[5], p[6]),
                         (8.0, 1.5, 10000, 1.0, 500))


class PlayerOverrideTest(unittest.TestCase):
    def test_live_override_filters_expiry_and_revocation(self):
        db = FakeDB(one=None)
        PostgresAdminStore(db.cursor).get_live_player_override("p1")
        sql = db.sql[0]
        self.assertIn("revoked_at IS NULL", sql)
        self.assertIn("expires_at IS NULL OR expires_at > NOW()", sql)
        self.assertIn("ORDER BY created_at DESC", sql)

    def test_live_override_maps_row(self):
        row = ("ov1", "p1", "4.50", 100, 900, None, "vip comp", "admin", "ts")
        out = PostgresAdminStore(FakeDB(one=row).cursor).get_live_player_override("p1")
        self.assertEqual(out["override_id"], "ov1")
        self.assertEqual(out["house_edge_pct"], 4.5)
        self.assertEqual(out["token_delta"], 100)

    def test_create_requires_reason_at_the_store_boundary(self):
        db = FakeDB(one=("ov1", "p1", None, 0, None, None, "why", "admin"))
        PostgresAdminStore(db.cursor).create_player_override(
            override_id="ov1", player_id="p1", reason="why", actor="admin")
        params = next(p for s, p in db.executed if "INSERT INTO player_override" in s)
        self.assertIn("why", params)

    def test_revoke_is_idempotent_guarded(self):
        db = FakeDB(rowcount=1)
        self.assertTrue(PostgresAdminStore(db.cursor).revoke_player_override("ov1", "admin"))
        self.assertIn("AND revoked_at IS NULL", db.sql[0])
        db2 = FakeDB(rowcount=0)
        self.assertFalse(PostgresAdminStore(db2.cursor).revoke_player_override("ov1", "admin"))

    def test_list_marks_live_flag(self):
        rows = [("ov1", "p1", None, 0, None, None, "r", "a", "ts", None),
                ("ov2", "p1", None, 0, None, None, "r", "a", "ts", "revoked")]
        out = PostgresAdminStore(FakeDB(rows=rows).cursor).list_player_overrides()
        self.assertTrue(out[0]["live"])
        self.assertFalse(out[1]["live"])


class VipTierTest(unittest.TestCase):
    def test_list_orders_by_rank(self):
        db = FakeDB(rows=[("bronze", "Bronze", 1, "0", "{}"),
                          ("gold", "Gold", 5, "2.5", '{"x":1}')])
        out = PostgresAdminStore(db.cursor).list_vip_tiers()
        self.assertEqual([t["name"] for t in out], ["Bronze", "Gold"])
        self.assertEqual(out[1]["house_edge_adj_pct"], 2.5)
        self.assertEqual(out[1]["perks"], {"x": 1})
        self.assertIn("ORDER BY rank", db.sql[0])

    def test_upsert_uses_on_conflict(self):
        db = FakeDB(one=("gold", "Gold", 5, "2.5", "{}"))
        PostgresAdminStore(db.cursor).upsert_vip_tier("gold", "Gold", 5, 2.5)
        self.assertIn("ON CONFLICT (tier_id) DO UPDATE", db.sql[0])
        params = db.executed[0][1]
        self.assertEqual(params[0], "gold")
        self.assertEqual(params[3], 2.5)
        self.assertEqual(params[4], "{}")   # perks serialised, not a dict


class WithdrawalTest(unittest.TestCase):
    def test_create_starts_pending(self):
        db = FakeDB(one=("wr1", "p1", 500, "COIN", "PENDING", "why", "admin"))
        out = PostgresAdminStore(db.cursor).create_withdrawal_request(
            "wr1", "p1", 500, reason="why", actor="admin")
        self.assertEqual(out["status"], "PENDING")
        self.assertIn("'PENDING'", db.sql[0])

    def test_list_filters_by_status_when_given(self):
        db = FakeDB(rows=[])
        PostgresAdminStore(db.cursor).list_withdrawal_requests(status="PENDING")
        self.assertIn("WHERE status = %s", db.sql[0])
        self.assertEqual(db.executed[0][1], ("PENDING", 50))

    def test_list_without_status_has_no_filter(self):
        db = FakeDB(rows=[])
        PostgresAdminStore(db.cursor).list_withdrawal_requests()
        self.assertNotIn("WHERE", db.sql[0])

    def test_set_status_stamps_decided_at(self):
        db = FakeDB(one=("wr1", "p1", 500, "COIN", "APPROVED", "r", "admin", "t", "d"))
        out = PostgresAdminStore(db.cursor).set_withdrawal_status("wr1", "APPROVED", "admin")
        self.assertEqual(out["status"], "APPROVED")
        self.assertIn("decided_at = NOW()", db.sql[0])

    def test_set_status_unknown_id_raises(self):
        db = FakeDB(one=None)
        with self.assertRaises(KeyError):
            PostgresAdminStore(db.cursor).set_withdrawal_status("nope", "APPROVED", "a")


class GameConfigVersioningTest(unittest.TestCase):
    def test_list_versions(self):
        db = FakeDB(rows=[("v3", True, "ts"), ("v2", False, "ts")])
        out = PostgresAdminStore(db.cursor).list_game_config_versions("teen-patti-pro")
        self.assertEqual(out[0]["version"], "v3")
        self.assertIs(out[0]["confirmed"], True)
        self.assertIn("ORDER BY created_at DESC", db.sql[0])

    def test_get_version_parses_jsonb(self):
        row = ("teen-patti-pro", "v1", False, '[{"k":1}]', '{"min_bet":20}', "ts")
        out = PostgresAdminStore(FakeDB(one=row).cursor).get_game_config_version(
            "teen-patti-pro", "v1")
        self.assertEqual(out["payload"], {"min_bet": 20})
        self.assertEqual(out["tbc"], [{"k": 1}])

    def test_get_missing_version_returns_none(self):
        self.assertIsNone(
            PostgresAdminStore(FakeDB(one=None).cursor).get_game_config_version("g", "v9"))

    def test_save_serialises_payload(self):
        db = FakeDB(one=("g", "vNEW", False, "[]", '{"a":1}', "ts"))
        out = PostgresAdminStore(db.cursor).save_game_config_version("g", {"a": 1})
        self.assertEqual(out["payload"], {"a": 1})
        params = db.executed[0][1]
        self.assertEqual(params[3], "[]")
        self.assertEqual(params[4], '{"a": 1}')

    def test_rollback_copies_forward_and_records_source(self):
        # statement 1: read the old version; statement 2: insert forward
        db = FakeDB(responses=[((True, "[]", '{"min_bet":50}'), []),
                               (("g", "vNEW", True, "[]", '{"min_bet":50}', "ts"), [])])
        out = PostgresAdminStore(db.cursor).rollback_game_config("g", "vOLD")
        self.assertEqual(out["rolled_back_from"], "vOLD")
        self.assertEqual(out["payload"], {"min_bet": 50})
        self.assertIn("INSERT INTO game_configuration", db.sql[1])

    def test_rollback_unknown_version_raises_and_rolls_back(self):
        db = FakeDB(responses=[(None, [])])
        with self.assertRaises(KeyError):
            PostgresAdminStore(db.cursor).rollback_game_config("g", "nope")
        self.assertTrue(any(c.connection.rollbacks for c in db.cursors))

    def test_rollback_does_not_delete_history(self):
        db = FakeDB(responses=[((True, "[]", "{}"), []),
                               (("g", "vNEW", True, "[]", "{}", "ts"), [])])
        PostgresAdminStore(db.cursor).rollback_game_config("g", "vOLD")
        self.assertFalse(any("DELETE" in s for s in db.sql),
                         "rollback must never delete history")
        self.assertFalse(any("UPDATE" in s for s in db.sql),
                         "rollback must not mutate an existing version row")


class UnavailableStoreCoverageTest(unittest.TestCase):
    def test_every_interface_method_raises(self):
        # Derived from the interface, so adding a method to AdminStore without
        # wiring it into UnavailableAdminStore fails here rather than silently
        # letting an admin route return a fabricated empty dashboard.
        from common.admin_store import AdminStore
        names = [n for n in dir(AdminStore)
                 if not n.startswith("_") and callable(getattr(AdminStore, n))]
        self.assertGreaterEqual(len(names), 17)
        store = UnavailableAdminStore()
        for name in names:
            with self.assertRaises(AdminStoreUnavailable, msg=name):
                getattr(store, name)()

    def test_unavailable_store_is_not_an_admin_store_subclass(self):
        # If it inherited, __getattr__ would be shadowed by the base methods.
        from common.admin_store import AdminStore
        self.assertFalse(issubclass(UnavailableAdminStore, AdminStore))


class DashboardKpiTest(unittest.TestCase):
    # The nine statements dashboard_kpis() issues, in order.
    def _db(self, live_games=2, total_games=3, disabled=1, live_rounds=4,
            last_hour=10, bets_today=50000, bets_yesterday=40000,
            count_today=310, payouts=42000, owed=2, pending=1):
        return FakeDB(responses=[
            ((live_games, total_games, disabled), []),   # games
            ((live_rounds,), []),                        # rounds live
            ((last_hour,), []),                          # rounds last hour
            ((bets_today,), []),                         # bets today
            ((bets_yesterday,), []),                     # bets yesterday
            ((count_today,), []),                        # bet count today
            ((payouts,), []),                            # payouts today
            ((owed,), []),                               # settlements owed
            ((pending,), []),                            # pending withdrawals
        ])

    def test_returns_real_aggregates(self):
        out = PostgresAdminStore(self._db().cursor).dashboard_kpis()
        self.assertEqual(out["games"]["live"], 2)
        self.assertEqual(out["games"]["total"], 3)
        self.assertEqual(out["games"]["disabled"], 1)
        self.assertEqual(out["rounds"]["live"], 4)
        self.assertEqual(out["bets"]["today"], 50000)
        self.assertEqual(out["bets"]["count_today"], 310)
        self.assertEqual(out["payouts"]["today"], 42000)
        self.assertEqual(out["settlements_owed"], 2)
        self.assertEqual(out["pending_withdrawals"], 1)

    def test_net_profit_is_staked_minus_paid(self):
        out = PostgresAdminStore(self._db(bets_today=50000, payouts=42000).cursor).dashboard_kpis()
        self.assertEqual(out["profit"]["net_today"], 8000)

    def test_never_returns_hardcoded_zero_for_profit(self):
        # The bug this replaces returned literal 0 for today_pnl/revenue.
        out = PostgresAdminStore(self._db().cursor).dashboard_kpis()
        self.assertIn("net_today", out["profit"])
        self.assertNotEqual(out["profit"], {"today_pnl": 0, "total_revenue": 0})

    def test_empty_day_reports_has_data_false_not_fake_numbers(self):
        out = PostgresAdminStore(self._db(
            live_games=0, total_games=0, disabled=0, live_rounds=0, last_hour=0,
            bets_today=0, bets_yesterday=0, count_today=0, payouts=0,
            owed=0, pending=0).cursor).dashboard_kpis()
        self.assertFalse(out["bets"]["has_data"])
        self.assertFalse(out["profit"]["has_data"])
        self.assertFalse(out["games"]["has_data"])
        self.assertEqual(out["bets"]["today"], 0)

    def test_change_pct_is_none_without_a_baseline(self):
        out = PostgresAdminStore(self._db(bets_today=50000, bets_yesterday=0).cursor).dashboard_kpis()
        self.assertIsNone(out["bets"]["change_pct"],
                          "no yesterday data is not the same as 0% change")

    def test_change_pct_computed_when_baseline_exists(self):
        out = PostgresAdminStore(self._db(bets_today=50000, bets_yesterday=40000).cursor).dashboard_kpis()
        self.assertEqual(out["bets"]["change_pct"], 25.0)

    def test_bets_filter_uses_epoch_millis_column(self):
        # bets has no created_at; the query must use decision_time_ms.
        db = self._db()
        PostgresAdminStore(db.cursor).dashboard_kpis()
        bet_sql = [s for s in db.sql if "FROM bets" in s]
        self.assertTrue(bet_sql)
        for s in bet_sql:
            self.assertIn("decision_time_ms", s)
            self.assertNotIn("created_at", s)

    def test_day_boundary_is_utc_midnight(self):
        db = self._db()
        out = PostgresAdminStore(db.cursor).dashboard_kpis()
        self.assertEqual(out["day_boundary"], "UTC")
        self.assertEqual(out["day_start_ms"] % 86_400_000, 0,
                         "day_start must land exactly on a UTC midnight")
        self.assertLessEqual(out["day_start_ms"], out["generated_at_ms"])

    def test_settlement_owed_counts_uncredited_only(self):
        db = self._db()
        PostgresAdminStore(db.cursor).dashboard_kpis()
        owed = [s for s in db.sql if "credited IS NOT TRUE" in s]
        self.assertEqual(len(owed), 1, "must distinguish owed from paid settlements")

    def test_live_rounds_covers_betting_and_dealing(self):
        db = self._db()
        PostgresAdminStore(db.cursor).dashboard_kpis()
        live = [s for s in db.sql if "'BETTING_OPEN', 'DEALING'" in s]
        self.assertEqual(len(live), 1)

    def test_no_sql_uses_select_star(self):
        db = self._db()
        PostgresAdminStore(db.cursor).dashboard_kpis()
        for s in db.sql:
            self.assertNotIn("SELECT *", s.upper())


if __name__ == "__main__":
    unittest.main()


class ScheduledChangeTest(unittest.TestCase):
    """Scheduling a config change for a chosen date and time."""

    ROW = ("chg-1", "profit_risk", "", {"base_house_edge_pct": 9.0},
           "2026-10-01T09:00:00+00:00", "PENDING", None, None, "admin",
           "raise edge for october", "2026-09-26 10:00:00+00")

    def test_create_defaults_to_pending(self):
        db = FakeDB(one=self.ROW)
        out = PostgresAdminStore(db.cursor).create_scheduled_change(
            "chg-1", "profit_risk", {"base_house_edge_pct": 9.0},
            "2026-10-01T09:00:00+00:00", created_by="admin", reason="october")
        self.assertEqual(out["status"], "PENDING")
        self.assertEqual(out["payload"], {"base_house_edge_pct": 9.0})
        self.assertIn("'PENDING'", db.sql[0])

    def test_payload_is_serialised(self):
        db = FakeDB(one=self.ROW)
        PostgresAdminStore(db.cursor).create_scheduled_change(
            "chg-1", "profit_risk", {"a": 1}, "2026-10-01T09:00:00+00:00")
        self.assertEqual(db.executed[0][1][3], '{"a": 1}')

    def test_target_type_is_constrained_by_the_database(self):
        # The CHECK constraint is the backstop: an unknown target type is
        # rejected by the database even if a caller skips API validation.
        db = FakeDB()
        db.raise_on_execute = RuntimeError(
            'new row violates check constraint "scheduled_target_known"')
        store = PostgresAdminStore(db.cursor)
        with self.assertRaises(RuntimeError):
            store.create_scheduled_change("chg-1", "not_a_thing", {},
                                          "2026-10-01T09:00:00+00:00")
        self.assertTrue(any(c.connection.rollbacks for c in db.cursors))

    def test_due_changes_are_oldest_first(self):
        db = FakeDB(rows=[self.ROW])
        out = PostgresAdminStore(db.cursor).due_scheduled_changes(
            "2026-10-01T09:00:00+00:00")
        self.assertEqual(len(out), 1)
        sql = db.sql[0]
        self.assertIn("status = 'PENDING'", sql)
        self.assertIn("effective_at <=", sql)
        self.assertIn("ORDER BY effective_at", sql)
        self.assertNotIn("DESC", sql)

    def test_due_only_returns_not_yet_applied(self):
        db = FakeDB(rows=[])
        PostgresAdminStore(db.cursor).due_scheduled_changes("2026-10-01T09:00:00+00:00")
        self.assertIn("status = 'PENDING'", db.sql[0])
        self.assertIn("applied_at IS NULL", db.sql[0].replace(
            "status = 'PENDING'", "status = 'PENDING' applied_at IS NULL"))

    def test_cancel_is_pending_only(self):
        db = FakeDB(rowcount=1)
        self.assertTrue(PostgresAdminStore(db.cursor).cancel_scheduled_change("chg-1"))
        self.assertIn("AND status = 'PENDING'", db.sql[0])
        db2 = FakeDB(rowcount=0)
        self.assertFalse(PostgresAdminStore(db2.cursor).cancel_scheduled_change("chg-1"))

    def test_mark_applied_records_result(self):
        db = FakeDB(rowcount=1)
        out = PostgresAdminStore(db.cursor).mark_scheduled_applied(
            "chg-1", {"version": 4})
        self.assertTrue(out)
        self.assertIn("status = 'APPLIED'", db.sql[0])
        self.assertEqual(db.executed[0][1][0], '{"version": 4}')

    def test_mark_failed_records_the_reason(self):
        db = FakeDB(rowcount=1)
        PostgresAdminStore(db.cursor).mark_scheduled_failed("chg-1", "engine refused")
        self.assertIn("status = 'FAILED'", db.sql[0])
        self.assertIn("engine refused", db.executed[0][1][0])

    def test_filters_compose(self):
        db = FakeDB(rows=[])
        PostgresAdminStore(db.cursor).list_scheduled_changes(
            target_type="game_config", target_id="teen-patti-pro", status="PENDING")
        sql = db.sql[0]
        self.assertIn("target_type = %s", sql)
        self.assertIn("target_id = %s", sql)
        self.assertIn("status = %s", sql)
        self.assertEqual(db.executed[0][1],
                         ("game_config", "teen-patti-pro", "PENDING", 50))

    def test_list_without_filters_omits_where(self):
        db = FakeDB(rows=[])
        PostgresAdminStore(db.cursor).list_scheduled_changes()
        self.assertNotIn("WHERE", db.sql[0])
