"""RBAC hierarchy + config audit fields (deployability §9-§10).

Roles: admin > operator > auditor. Auditor reads admin
endpoints; operator also runs round lifecycle; admin alone writes
config. Config updates record before/after/reason/updated_by/applied_at.
"""
import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet
from games.teen_patti_pro import api as api_mod
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService
from tests.test_asset_lifecycle import CountedCase


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if body is not None and method in ("POST", "PUT"):
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


OP = {"X-Admin-Key": "t-op"}
RO = {"X-Admin-Key": "t-ro"}
AD = {"X-Admin-Key": "t-adm"}
SU = {"X-Admin-Key": "t-sup"}
BAD = {"X-Admin-Key": "t-bogus"}


class RBACTest(CountedCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = dict(api_mod.ADMIN_KEYS)
        api_mod.ADMIN_KEYS.clear()
        api_mod.ADMIN_KEYS.update({"t-op": "operator", "t-ro": "auditor",
                                   "t-adm": "admin", "t-sup": "admin",
                                   "t-bogus": "intern"})
        w = MemoryWallet()
        Handler.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=w)
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        api_mod.ADMIN_KEYS.clear()
        api_mod.ADMIN_KEYS.update(cls._saved)

    def test_auditor_reads_but_cannot_operate(self):
        st, _ = call("GET", self.base + "/api/v1/admin/games", headers=RO)
        self.counted(st == 200, "auditor reads inventory")
        st, _ = call("GET", self.base + "/api/v1/admin/audit?limit=3", headers=RO)
        self.counted(st == 200, "auditor reads audit")
        st, body = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/r1/rounds/start",
                        {}, RO)
        self.counted(st == 403, f"auditor denied rounds op: {body.get('code')}")
        st, _ = call("PUT", self.base + "/api/v1/admin/games/teen-patti-pro/config",
                     {"enabled": True}, RO)
        self.counted(st == 403, "auditor denied config write")

    def test_operator_operates_but_cannot_configure(self):
        st, _ = call("POST", self.base + "/api/v1/games/teen-patti-pro/rooms/r2/rounds/start",
                     {}, OP)
        self.counted(st == 200, "operator starts rounds")
        st, _ = call("PUT", self.base + "/api/v1/admin/games/teen-patti-pro/config",
                     {"enabled": True}, OP)
        self.counted(st == 403, "operator denied config write")

    def test_unknown_role_and_missing_key_denied(self):
        st, _ = call("GET", self.base + "/api/v1/admin/games", headers=BAD)
        self.counted(st == 403, "unknown role denied")
        st, _ = call("GET", self.base + "/api/v1/admin/games")
        self.counted(st == 403, "missing key denied")

    def test_admin_config_write_audits_before_reason(self):
        st, body = call("PUT", self.base + "/api/v1/admin/games/teen-patti-pro/config",
                        {"enabled": False, "reason": "maintenance window"}, SU)
        self.counted(st == 200, "admin writes config")
        entries = Handler.svc.audit.list("game", 5)
        updates = [e for e in entries if e["action"] == "config.update"]
        self.counted(len(updates) >= 1, "config.update audited")
        last = updates[-1]
        self.counted(last["actor"] == "admin", "actor recorded")
        self.counted(last["after"].get("reason") == "maintenance window", "reason recorded")
        self.counted(last["after"].get("updated_by") == "admin", "updated_by recorded")
        self.counted("applied_at_ms" in last["after"], "applied_at recorded")
        self.counted("enabled" in (last.get("before") or {}), "before-image recorded")
        st, _ = call("PUT", self.base + "/api/v1/admin/games/teen-patti-pro/config",
                     {"enabled": True, "reason": "restore"}, SU)
        self.counted(st == 200, "config restored")


if __name__ == "__main__":
    unittest.main()
