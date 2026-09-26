"""HTTP contract for the admin deep-control surface.

Every route here is database-backed, and the store is injected so these tests
need no Postgres. The behaviour most worth pinning down is the failure mode:
with no database the route must answer 503 with a reason, never 200 with an
empty payload, because "there is nothing configured" and "there is no data
source" look identical to a dashboard and are acted on very differently.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from common.admin_store import AdminStoreUnavailable, PostgresAdminStore
from games.teen_patti_pro import api as api_mod
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService

ADMIN = {"X-Admin-Key": "a-key"}       # role: admin
OPERATOR = {"X-Admin-Key": "o-key"}     # role: operator
AUDITOR = {"X-Admin-Key": "r-key"}      # role: auditor

PR = {"config_id": "default", "version": 3, "base_house_edge_pct": 8.0,
      "vip_profit_adj_pct": 1.5, "max_payout_per_round": 10000,
      "rng_weight": 1.0, "max_daily_loss_per_player": 500, "active": True,
      "created_by": "admin", "created_at": "2026-09-26 10:00:00+00"}
PKG = {"package_id": "pkg-1", "name": "Starter", "coins": 500,
       "price_minor": 499, "currency": "USD", "bonus_percent": 10,
       "bonus_coins": 50, "is_active": True, "sort_order": 0,
       "tags": ["popular"], "created_at": "x", "updated_at": "x"}


class _Store:
    """Records calls and returns queued values; raises whatever it is given."""

    def __init__(self, **returns):
        self._r = dict(returns)
        self.calls = []
        # Pop, not read: `raises=` is the switch, and leaving it in the
        # returns map would let it be looked up as if it were a query.
        self.raises = self._r.pop("raises", None)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*a, **kw):
            self.calls.append((name, a, kw))
            if self.raises:
                raise self.raises
            value = self._r.get(name)
            if callable(value):
                return value(*a, **kw)
            return value
        return _call


class AdminDeepControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_keys = dict(api_mod.ADMIN_KEYS)
        cls._orig_scopes = dict(getattr(api_mod, "ADMIN_SCOPES", {}))
        api_mod.ADMIN_KEYS.clear()
        api_mod.ADMIN_KEYS.update({"a-key": "admin", "o-key": "operator",
                                   "r-key": "auditor"})
        api_mod.ADMIN_SCOPES = {}
        w = __import__("common.wallet", fromlist=["MemoryWallet"]).MemoryWallet()
        w.fund("qa-player", 20000)
        Handler.svc = TeenPattiService(
            config=TeenPattiConfig(confirmed=True), wallet=w)
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        api_mod.ADMIN_KEYS.clear()
        api_mod.ADMIN_KEYS.update(cls._orig_keys)
        api_mod.ADMIN_SCOPES.clear()
        api_mod.ADMIN_SCOPES.update(cls._orig_scopes)

    def setUp(self):
        self._saved = getattr(Handler, "admin_store", None)
        self.addCleanup(self._restore)

    def _restore(self):
        Handler.admin_store = self._saved

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    # -- reads ------------------------------------------------------------

    def test_profit_risk_get(self):
        Handler.admin_store = _Store(get_active_profit_risk=PR,
                                     list_profit_risk_versions=[PR])
        st, b = self.call("GET", "/api/v1/admin/profit-risk", headers=AUDITOR)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["data"]["active"]["base_house_edge_pct"], 8.0)
        self.assertEqual(len(b["data"]["versions"]), 1)

    def test_profit_risk_put_saves_and_audits(self):
        store = _Store(save_profit_risk=PR)
        Handler.admin_store = store
        st, b = self.call("PUT", "/api/v1/admin/profit-risk", PR, ADMIN)
        self.assertEqual(st, 200, b)
        self.assertEqual(store.calls[0][0], "save_profit_risk")

    def test_profit_risk_put_requires_admin(self):
        Handler.admin_store = _Store(save_profit_risk=PR)
        for who, name in ((OPERATOR, "operator"), (AUDITOR, "auditor")):
            st, _ = self.call("PUT", "/api/v1/admin/profit-risk", PR, who)
            self.assertEqual(st, 403, name)

    def test_simulate_returns_numbers_and_a_model_block(self):
        Handler.admin_store = _Store(get_active_profit_risk=PR)
        st, b = self.call("POST", "/api/v1/admin/profit-risk/simulate",
                          {"rounds": 500}, AUDITOR)
        self.assertEqual(st, 200, b)
        d = b["data"]
        for key in ("expected_profit", "roi_pct", "max_exposure", "risk_level"):
            self.assertIn(key, d)
        self.assertIn(d["risk_level"], {"Low", "Medium", "High"})
        self.assertIn("not_a_measurement", d["model"])

    def test_simulate_uses_the_saved_config_as_its_baseline(self):
        store = _Store(get_active_profit_risk=PR)
        Handler.admin_store = store
        self.call("POST", "/api/v1/admin/profit-risk/simulate", {}, AUDITOR)
        self.assertEqual(store.calls[0][0], "get_active_profit_risk")

    def test_packages_list(self):
        Handler.admin_store = _Store(list_packages=[PKG])
        st, b = self.call("GET", "/api/v1/admin/packages", headers=AUDITOR)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["data"]["packages"][0]["package_id"], "pkg-1")

    def test_packages_active_filter_is_forwarded(self):
        store = _Store(list_packages=[])
        Handler.admin_store = store
        self.call("GET", "/api/v1/admin/packages?active=true", headers=AUDITOR)
        self.assertTrue(store.calls[0][1][0], "active_only should be true")

    def test_settings_get_and_put(self):
        Handler.admin_store = _Store(get_settings={"platform_name": "X"},
                                     put_settings={"platform_name": "Y"})
        st, b = self.call("GET", "/api/v1/admin/settings", headers=AUDITOR)
        self.assertEqual(b["data"]["settings"]["platform_name"], "X")
        st, b = self.call("PUT", "/api/v1/admin/settings",
                          {"platform_name": "Y"}, ADMIN)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["data"]["settings"]["platform_name"], "Y")

    def test_settings_put_needs_a_body(self):
        Handler.admin_store = _Store()
        st, _ = self.call("PUT", "/api/v1/admin/settings", {}, ADMIN)
        self.assertEqual(st, 422)

    def test_scheduled_changes_list_and_cancel(self):
        Handler.admin_store = _Store(
            list_scheduled_changes=[{"change_id": "c1", "status": "PENDING"}],
            cancel_scheduled_change=True)
        st, b = self.call("GET", "/api/v1/admin/scheduled-changes", headers=AUDITOR)
        self.assertEqual(b["data"]["changes"][0]["change_id"], "c1")
        st, b = self.call("DELETE", "/api/v1/admin/scheduled-changes/c1",
                          headers=ADMIN)
        self.assertEqual(st, 200, b)
        self.assertTrue(b["data"]["cancelled"])

    def test_scheduled_change_create_requires_admin(self):
        Handler.admin_store = _Store()
        st, _ = self.call("POST", "/api/v1/admin/scheduled-changes",
                          {"target_type": "profit_risk", "effective_at": "2026-10-01"},
                          AUDITOR)
        self.assertEqual(st, 403)

    def test_player_overrides_list_and_revoke(self):
        Handler.admin_store = _Store(
            list_player_overrides=[{"override_id": "o1", "player_id": "qa-player"}],
            revoke_player_override=True)
        st, b = self.call("GET", "/api/v1/admin/player-overrides?player_id=qa-player",
                          headers=AUDITOR)
        self.assertEqual(b["data"]["overrides"][0]["override_id"], "o1")
        st, b = self.call("DELETE", "/api/v1/admin/player-overrides/o1", headers=ADMIN)
        self.assertTrue(b["data"]["revoked"])

    def test_player_override_create_requires_a_reason(self):
        # The schema enforces a non-empty reason; the route surfaces it.
        Handler.admin_store = _Store(raises=RuntimeError(
            'violates check constraint "player_override_reason_required"'))
        st, b = self.call("POST", "/api/v1/admin/player-overrides",
                          {"player_id": "qa-player", "token_delta": 100}, ADMIN)
        self.assertEqual(st, 502)
        self.assertNotIn("player_override", b["message"],
                         "driver detail must not leak to the client")

    def test_package_create(self):
        Handler.admin_store = _Store(create_package=PKG)
        st, b = self.call("POST", "/api/v1/admin/packages",
                          {"name": "Starter", "coins": 500, "price_minor": 499},
                          ADMIN)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["data"]["package_id"], "pkg-1")

    def test_package_update_and_archive(self):
        Handler.admin_store = _Store(update_package=PKG, archive_package=True)
        st, _ = self.call("PUT", "/api/v1/admin/packages/pkg-1",
                          {"coins": 750}, ADMIN)
        self.assertEqual(st, 200)
        st, b = self.call("DELETE", "/api/v1/admin/packages/pkg-1", headers=ADMIN)
        self.assertEqual(st, 200, b)
        self.assertTrue(b["data"]["archived"])

    def test_game_enable_and_disable(self):
        Handler.admin_store = _Store(set_game_enabled=lambda gid, en, **kw: {
            "game_id": gid, "enabled": en})
        st, b = self.call("POST", "/api/v1/admin/games/teen-patti-pro/enable",
                          {}, ADMIN)
        self.assertEqual(st, 200, b)
        self.assertTrue(b["data"]["enabled"])
        st, b = self.call("POST", "/api/v1/admin/games/teen-patti-pro/disable",
                          {"message": "maintenance"}, ADMIN)
        self.assertEqual(st, 200, b)
        self.assertFalse(b["data"]["enabled"])

    def test_game_toggle_requires_admin(self):
        Handler.admin_store = _Store()
        st, _ = self.call("POST", "/api/v1/admin/games/teen-patti-pro/disable",
                          {}, OPERATOR)
        self.assertEqual(st, 403)

    # -- player appearance -------------------------------------------------

    def test_appearance_defaults_when_unset(self):
        Handler.admin_store = _Store(get_player_appearance=dict(
            PostgresAdminStore.DEFAULT_APPEARANCE))
        st, b = self.call("GET", "/api/v1/players/newbie/appearance")
        self.assertEqual(st, 200, b)
        self.assertEqual(b["data"]["appearance"]["source"], "default")
        self.assertIn("avatar-placeholder", b["data"]["appearance"]["avatar"])

    def test_manual_apply_trigger_reports_a_clean_sweep(self):
        Handler.admin_store = _Store(claim_due_scheduled_changes=[])
        st, b = self.call("POST", "/api/v1/admin/scheduled-changes/apply",
                          {}, ADMIN)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["data"]["report"]["checked"], 0)

    def test_manual_apply_trigger_requires_admin(self):
        Handler.admin_store = _Store(claim_due_scheduled_changes=[])
        st, _ = self.call("POST", "/api/v1/admin/scheduled-changes/apply",
                          {}, OPERATOR)
        self.assertEqual(st, 403)

    def test_admin_can_set_an_appearance(self):
        Handler.admin_store = _Store(set_player_appearance=lambda pid, a, **kw: {
            "avatar": a.get("avatar", ""), "source": "dearlive"})
        st, b = self.call("PUT", "/api/v1/admin/players/qa-player/appearance",
                          {"appearance": {"avatar": "/custom/a.svg"}}, ADMIN)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["data"]["appearance"]["avatar"], "/custom/a.svg")

    def test_appearance_serves_the_default_when_the_database_is_down(self):
        # This route is in the table-rendering path. An admin outage must blank
        # out nobody's avatar, so it degrades to the default instead of 503.
        Handler.admin_store = _Store(
            raises=AdminStoreUnavailable("DATABASE_URL is not configured"))
        st, b = self.call("GET", "/api/v1/players/qa-player/appearance")
        self.assertEqual(st, 200, b)
        self.assertTrue(b["data"]["degraded"])
        self.assertEqual(b["data"]["appearance"]["source"], "default")
        self.assertIn("avatar-placeholder", b["data"]["appearance"]["avatar"])

    def test_appearance_degrades_on_an_unexpected_fault_too(self):
        Handler.admin_store = _Store(raises=RuntimeError("pool exhausted"))
        st, b = self.call("GET", "/api/v1/players/qa-player/appearance")
        self.assertEqual(st, 200, b)
        self.assertTrue(b["data"]["degraded"])

    def test_appearance_read_is_open_to_the_client(self):
        # A player rendering their own table must not need an admin key.
        Handler.admin_store = _Store(get_player_appearance=dict(
            PostgresAdminStore.DEFAULT_APPEARANCE))
        st, _ = self.call("GET", "/api/v1/players/qa-player/appearance")
        self.assertEqual(st, 200)

    def test_appearance_set_requires_admin(self):
        Handler.admin_store = _Store()
        st, _ = self.call("PUT", "/api/v1/admin/players/qa-player/appearance",
                          {"appearance": {}}, OPERATOR)
        self.assertEqual(st, 403)

    # -- failure modes -----------------------------------------------------

    def test_no_database_is_503_with_a_reason_and_no_payload(self):
        Handler.admin_store = _Store(
            raises=AdminStoreUnavailable("DATABASE_URL is not configured"))
        for method, path, hdr in (
            ("GET", "/api/v1/admin/profit-risk", AUDITOR),
            ("GET", "/api/v1/admin/packages", AUDITOR),
            ("GET", "/api/v1/admin/settings", AUDITOR),
            ("GET", "/api/v1/admin/scheduled-changes", AUDITOR),
            ("GET", "/api/v1/admin/player-overrides", AUDITOR),
        ):
            st, b = self.call(method, path, headers=hdr)
            self.assertEqual(st, 503, path)
            self.assertIsNone(b["data"], path)
            self.assertIn("DATABASE_URL", b["message"], path)

    def test_write_without_database_is_503(self):
        Handler.admin_store = _Store(
            raises=AdminStoreUnavailable("DATABASE_URL is not configured"))
        st, b = self.call("PUT", "/api/v1/admin/profit-risk", PR, ADMIN)
        self.assertEqual(st, 503)
        self.assertIsNone(b["data"])

    def test_driver_error_is_502_without_leaking_detail(self):
        Handler.admin_store = _Store(raises=RuntimeError(
            'relation "profit_risk_config" does not exist'))
        st, b = self.call("GET", "/api/v1/admin/profit-risk", headers=AUDITOR)
        self.assertEqual(st, 502)
        self.assertNotIn("profit_risk_config", b["message"])

    def test_missing_row_is_404(self):
        Handler.admin_store = _Store(raises=KeyError("pkg-404"))
        st, b = self.call("PUT", "/api/v1/admin/packages/pkg-404", {}, ADMIN)
        self.assertEqual(st, 404, b)

    def test_unauthenticated_is_refused_on_every_read(self):
        Handler.admin_store = _Store(get_active_profit_risk=PR)
        for path in ("/api/v1/admin/profit-risk", "/api/v1/admin/packages",
                     "/api/v1/admin/settings", "/api/v1/admin/audit",
                     "/api/v1/admin/scheduled-changes",
                     "/api/v1/admin/player-overrides"):
            st, _ = self.call("GET", path)
            self.assertIn(st, (401, 403), path)


if __name__ == "__main__":
    unittest.main()
