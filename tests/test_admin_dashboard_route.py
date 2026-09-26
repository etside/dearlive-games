"""Route-level tests for GET /api/v1/admin/dashboard.

The store itself is covered in tests/test_admin_store.py with a fake cursor.
These tests cover the HTTP contract around it, which is where the original
defect lived: the dashboard used to answer 200 with hardcoded zeros, so a
missing database looked exactly like a quiet trading day.

The store is injected in-process, so these tests need no database.
"""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.admin_store import AdminStoreUnavailable  # noqa: E402

AUDITOR = {"X-Admin-Key": "dev-auditor-key"}
OPERATOR = {"X-Admin-Key": "dev-operator-key"}

KPIS = {
    "generated_at_ms": 1790000000000,
    "day_start_ms": 1789929600000,
    "day_boundary": "UTC",
    "games": {"live": 2, "total": 3, "disabled": 1, "has_data": True},
    "rounds": {"live": 4, "last_hour": 10, "has_data": True},
    "bets": {"today": 50000, "yesterday": 40000, "count_today": 310,
             "has_data": True, "change_pct": 25.0},
    "payouts": {"today": 42000, "has_data": True},
    "profit": {"net_today": 8000, "net_yesterday": 40000, "has_data": True,
               "change_pct": -80.0},
    "settlements_owed": 2,
    "pending_withdrawals": 1,
}


class _StubStore:
    def __init__(self, result=None, raises=None):
        self._result = result if result is not None else KPIS
        self._raises = raises

    def dashboard_kpis(self):
        if self._raises:
            raise self._raises
        return self._result


def _wsgi_call(method, path, body=None, headers=None, query=""):
    from staging import wsgi as W
    data = json.dumps(body).encode() if body is not None else None
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
           "CONTENT_LENGTH": str(len(data or b"")),
           "CONTENT_TYPE": "application/json",
           "wsgi.input": io.BytesIO(data or b""), "wsgi.errors": io.StringIO()}
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    captured = {}

    def start_response(status, rheaders):
        captured["status"] = status

    payload = b"".join(W.app(env, start_response))
    return int(captured["status"].split()[0]), json.loads(payload or b"null")


class AdminDashboardRouteTest(unittest.TestCase):
    def setUp(self):
        from games.teen_patti_pro.api import Handler
        self._handler = Handler
        self._saved = getattr(Handler, "admin_store", None)
        Handler.admin_store = _StubStore()
        self.addCleanup(self._restore)

    def _restore(self):
        self._handler.admin_store = self._saved

    def test_returns_real_kpis(self):
        status, body = _wsgi_call("GET", "/api/v1/admin/dashboard", headers=AUDITOR)
        self.assertEqual(status, 200, body)
        data = body["data"]
        self.assertEqual(data["bets"]["today"], 50000)
        self.assertEqual(data["profit"]["net_today"], 8000)
        self.assertEqual(data["games"]["live"], 2)
        self.assertTrue(data["bets"]["has_data"])

    def test_uses_the_standard_envelope(self):
        status, body = _wsgi_call("GET", "/api/v1/admin/dashboard", headers=AUDITOR)
        self.assertEqual(status, 200)
        for key in ("success", "code", "message", "data", "serverTime", "requestId"):
            self.assertIn(key, body, f"envelope missing {key}")

    def test_no_database_is_503_not_zeros(self):
        self._handler.admin_store = _StubStore(
            raises=AdminStoreUnavailable("DATABASE_URL is not configured"))
        status, body = _wsgi_call("GET", "/api/v1/admin/dashboard", headers=AUDITOR)
        self.assertEqual(status, 503, body)
        self.assertIsNone(body["data"], "must not fabricate data on 503")
        self.assertIn("DATABASE_URL", body["message"])

    def test_query_failure_is_502_not_500(self):
        # A broken query is a bad gateway, not a bug in this service.
        self._handler.admin_store = _StubStore(raises=RuntimeError("syntax error"))
        status, body = _wsgi_call("GET", "/api/v1/admin/dashboard", headers=AUDITOR)
        self.assertEqual(status, 502, body)
        self.assertIn("RuntimeError", body["message"])

    def test_internal_error_detail_is_not_leaked_to_clients(self):
        # The type may surface; the driver's message must not.
        self._handler.admin_store = _StubStore(
            raises=RuntimeError('relation "secret_table" does not exist'))
        status, body = _wsgi_call("GET", "/api/v1/admin/dashboard", headers=AUDITOR)
        self.assertEqual(status, 502)
        self.assertNotIn("secret_table", body["message"])

    def test_requires_an_admin_key(self):
        status, body = _wsgi_call("GET", "/api/v1/admin/dashboard")
        self.assertIn(status, (401, 403), body)
        self.assertIsNone(body["data"])

    def test_empty_day_is_flagged_rather_than_zeroed(self):
        empty = dict(KPIS, bets={"today": 0, "yesterday": 0, "count_today": 0,
                                 "has_data": False, "change_pct": None},
                     profit={"net_today": 0, "net_yesterday": 0,
                             "has_data": False, "change_pct": None})
        self._handler.admin_store = _StubStore(result=empty)
        status, body = _wsgi_call("GET", "/api/v1/admin/dashboard", headers=AUDITOR)
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["bets"]["has_data"],
                         "UI needs this to render 'No data yet'")
        self.assertIsNone(body["data"]["bets"]["change_pct"])


if __name__ == "__main__":
    unittest.main()
