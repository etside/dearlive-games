"""HTTP contract for the admin demo-room + player-session surface.

These routes let an operator run a demo without touching a shell: create a
room, mint per-player launch URLs (singly or in bulk), list what is live, and
revoke a session. Every one of them is exercised here through a real HTTP
server, because the failure that matters is a wrong status code or a URL
that does not play -- not a Python exception in a helper.
"""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from games.teen_patti_pro import api as api_mod
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService
from provider.sessions import MemorySessionTokenStore

ADMIN = {"X-Admin-Key": "a-key"}       # role: admin
AUDITOR = {"X-Admin-Key": "r-key"}     # role: auditor


class AdminRoomsSessionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_keys = dict(api_mod.ADMIN_KEYS)
        cls._orig_scopes = dict(getattr(api_mod, "ADMIN_SCOPES", {}))
        api_mod.ADMIN_KEYS.clear()
        api_mod.ADMIN_KEYS.update({"a-key": "admin", "r-key": "auditor"})
        api_mod.ADMIN_SCOPES = {}
        w = __import__("common.wallet", fromlist=["MemoryWallet"]).MemoryWallet()
        Handler.svc = TeenPattiService(
            config=TeenPattiConfig(confirmed=True), wallet=w)
        Handler.game_enabled = {}
        Handler.provider_tokens = MemorySessionTokenStore()
        Handler.provider_ctx = None  # forces the Host-header base fallback
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
        Handler.admin_rooms.clear()
        Handler.admin_sessions.clear()
        Handler._ratelimit_hits.clear()

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

    def test_admin_create_room(self):
        st, body = self.call("POST", "/api/v1/admin/rooms",
                             {"name": "Friday Demo", "currency": "COIN",
                              "chip_denoms": [1000, 10000],
                              "betting_duration_sec": 30},
                             ADMIN)
        self.assertEqual(st, 200, body)
        data = body["data"]
        self.assertEqual(data["room_id"], "friday-demo", data)
        self.assertIn("/teen-patti-pro?operator=demo&room=friday-demo",
                      data["join_url"], data)
        self.assertIn("created_at", data)

    def test_admin_list_rooms(self):
        self.call("POST", "/api/v1/admin/rooms", {"name": "List Me"}, ADMIN)
        st, body = self.call("GET", "/api/v1/admin/rooms", None, AUDITOR)
        self.assertEqual(st, 200, body)
        rooms = body["data"]["rooms"]
        self.assertEqual(len(rooms), 1, rooms)
        self.assertEqual(rooms[0]["room_id"], "list-me")
        self.assertIn("players_count", rooms[0])
        self.assertTrue(rooms[0]["active"])

    def test_admin_delete_room(self):
        self.call("POST", "/api/v1/admin/rooms", {"name": "Gone Soon"}, ADMIN)
        st, body = self.call("DELETE", "/api/v1/admin/rooms/gone-soon",
                             None, ADMIN)
        self.assertEqual(st, 200, body)
        self.assertTrue(body["data"]["closed"])
        st, body = self.call("GET", "/api/v1/admin/rooms", None, AUDITOR)
        self.assertEqual(body["data"]["rooms"], [])
        st, _ = self.call("DELETE", "/api/v1/admin/rooms/gone-soon",
                          None, ADMIN)
        self.assertEqual(st, 404)

    def test_admin_mint_session(self):
        before = int(time.time() * 1000)
        st, body = self.call("POST", "/api/v1/admin/sessions/mint",
                             {"player_id": "mint_001",
                              "room": "teen-patti-low"}, ADMIN)
        self.assertEqual(st, 200, body)
        data = body["data"]
        self.assertIn("/api/v1/provider/launch/gst_", data["play_url"], data)
        self.assertTrue(data["session_id"], data)
        # default TTL is 24h, within a 60s tolerance for test slowness
        self.assertTrue(abs(data["expires_at_ms"] - before - 24 * 3600 * 1000)
                        < 120000, data)

    def test_admin_bulk_mint(self):
        st, body = self.call(
            "POST", "/api/v1/admin/sessions/bulk",
            {"player_ids": ["bulk_1", "bulk_2", "bulk_3"],
             "room": "teen-patti-low"}, ADMIN)
        self.assertEqual(st, 200, body)
        sessions = body["data"]["sessions"]
        self.assertEqual(len(sessions), 3, body)
        self.assertEqual(body["data"]["errors"], [])
        urls = {s["play_url"] for s in sessions}
        self.assertEqual(len(urls), 3, "tokens must be unique per player")

    def test_admin_list_sessions(self):
        self.call("POST", "/api/v1/admin/sessions/mint",
                  {"player_id": "listed_1", "room": "teen-patti-low"}, ADMIN)
        st, body = self.call("GET", "/api/v1/admin/sessions?active=true",
                             None, AUDITOR)
        self.assertEqual(st, 200, body)
        rows = body["data"]["sessions"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["status"], "active")
        self.assertEqual(rows[0]["player_id"], "listed_1")

    def test_admin_revoke_session(self):
        _, minted = self.call("POST", "/api/v1/admin/sessions/mint",
                              {"player_id": "revoke_me",
                               "room": "teen-patti-low"}, ADMIN)
        sid = minted["data"]["session_id"]
        token = minted["data"]["play_url"].rsplit("/", 1)[-1]
        st, body = self.call("DELETE", f"/api/v1/admin/sessions/{sid}",
                             None, ADMIN)
        self.assertEqual(st, 200, body)
        self.assertTrue(body["data"]["revoked"])
        # the bearer credential is dead everywhere at once
        self.assertIsNone(Handler.provider_tokens.get(token))
        st, _ = self.call("DELETE", f"/api/v1/admin/sessions/{sid}",
                          None, ADMIN)
        # second revoke still finds the registry row and reports revoked
        self.assertEqual(st, 200)

    def test_revoked_session_rejected(self):
        _, minted = self.call("POST", "/api/v1/admin/sessions/mint",
                              {"player_id": "locked_out",
                               "room": "teen-patti-low"}, ADMIN)
        sid = minted["data"]["session_id"]
        token = minted["data"]["play_url"].rsplit("/", 1)[-1]
        self.call("DELETE", f"/api/v1/admin/sessions/{sid}", None, ADMIN)
        req = urllib.request.Request(
            self.base + "/api/v1/wallet/balance",
            headers={"Authorization": "Bearer " + token})
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("revoked token was still accepted")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401, "revoked token must 401")

    def test_admin_endpoints_require_auth(self):
        routes = [
            ("POST", "/api/v1/admin/rooms", {"name": "x"}),
            ("GET", "/api/v1/admin/rooms", None),
            ("DELETE", "/api/v1/admin/rooms/x", None),
            ("POST", "/api/v1/admin/sessions/mint",
             {"player_id": "x", "room": "y"}),
            ("POST", "/api/v1/admin/sessions/bulk",
             {"player_ids": ["x"], "room": "y"}),
            ("GET", "/api/v1/admin/sessions", None),
            ("DELETE", "/api/v1/admin/sessions/x", None),
        ]
        for method, path, body in routes:
            with self.subTest(method=method, path=path):
                st, _ = self.call(method, path, body, None)
                self.assertEqual(st, 403, f"{method} {path} allowed w/o key")

    def test_admin_actions_audited(self):
        self.call("POST", "/api/v1/admin/rooms", {"name": "Audited"}, ADMIN)
        _, minted = self.call("POST", "/api/v1/admin/sessions/mint",
                              {"player_id": "audited_1",
                               "room": "teen-patti-low"}, ADMIN)
        self.call("DELETE",
                  f"/api/v1/admin/sessions/{minted['data']['session_id']}",
                  None, ADMIN)
        actions = {(e["action"], e["entity"], e["entity_id"])
                   for e in Handler.svc.audit.entries}
        self.assertIn(("room.create", "room", "audited"), actions)
        self.assertIn(("session.mint", "session",
                       minted["data"]["session_id"]), actions)
        self.assertIn(("session.revoke", "session",
                       minted["data"]["session_id"]), actions)


if __name__ == "__main__":
    unittest.main()
