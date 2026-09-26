"""The repo assets/ static route.

This route exists so the default avatar URL that nobody ever configures still
resolves to a real file. The reason it needs its own tests is the reason it is
dangerous: the path segment comes straight off the URL, so a mistake here turns
a static file server into a filesystem read primitive.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService
from common.wallet import MemoryWallet


class _ServerCase(unittest.TestCase):
    """Boots one handler for a group of static-route tests."""

    @classmethod
    def setUpClass(cls):
        Handler.svc = TeenPattiService(
            config=TeenPattiConfig(confirmed=True), wallet=MemoryWallet())
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

class RepoAssetRouteTest(_ServerCase):
    def test_the_default_avatar_actually_exists(self):
        # The whole reason the route exists. If this file moves, the default
        # every unconfigured player gets silently stops rendering.
        st, ctype, body = self.get(
            "/assets/games/teen-patti-pro/avatars/avatar-placeholder.svg")
        self.assertEqual(st, 200)
        self.assertEqual(ctype, "image/svg+xml")
        self.assertIn(b"<svg", body)

    def test_the_default_frame_actually_exists(self):
        st, _, body = self.get(
            "/assets/games/teen-patti-pro/avatars/avatar-frame-navy.svg")
        self.assertEqual(st, 200)
        self.assertIn(b"<svg", body)

    def test_svg_is_served_as_svg_not_json(self):
        _, ctype, body = self.get(
            "/assets/games/teen-patti-pro/avatars/avatar-placeholder.svg")
        self.assertNotIn("json", ctype)
        self.assertFalse(body.lstrip().startswith(b'"'), "must not be JSON-encoded")

    def test_appearance_default_points_at_a_served_url(self):
        # Ties the two halves together: what the API advertises as the default
        # must be fetchable, or the client's onerror fallback is a dead end.
        st, _, body = self.get("/api/v1/players/whoever/appearance")
        self.assertEqual(st, 200)
        avatar = json.loads(body)["data"]["appearance"]["avatar"]
        st, _, _ = self.get(avatar)
        self.assertEqual(st, 200, f"default avatar {avatar} is not served")

    def test_traversal_is_refused(self):
        for evil in ("/assets/../.env",
                     "/assets/games/../../.env",
                     "/assets/games/teen-patti-pro/../../../etc/passwd",
                     "/assets//etc/passwd",
                     "/assets/games/./../../secrets"):
            st, _, body = self.get(evil)
            self.assertEqual(st, 404, evil)
            self.assertNotIn(b"root:", body, evil)

    def test_source_files_are_not_served(self):
        # .py and .env are inside the repo but must not be fetchable, even
        # though they live under a path this route can reach.
        for path in ("/assets/games/teen-patti-pro/api.py",
                     "/assets/games/teen-patti-pro/config.py",
                     "/assets/dearlive-master/../.env"):
            st, _, _ = self.get(path)
            self.assertEqual(st, 404, path)

    def test_missing_file_is_404(self):
        st, _, _ = self.get("/assets/games/teen-patti-pro/avatars/nope.svg")
        self.assertEqual(st, 404)

    def test_a_directory_is_not_listed(self):
        st, _, body = self.get("/assets/games/teen-patti-pro/avatars/")
        self.assertEqual(st, 404)
        self.assertNotIn(b"avatar-placeholder", body)


class ClientFileRouteTest(_ServerCase):
    """The /teen-patti-pro/<file> route.

    It used to be a hand-written list of four filenames, which is how
    lobby.html ended up unreachable. It is now pattern-based, so the path
    handling is what needs pinning down.
    """

    def test_client_files_are_served_by_extension(self):
        for path, ctype in (("/teen-patti-pro/how-to-play.html", "text/html"),
                            ("/teen-patti-pro/lobby.html", "text/html"),
                            ("/teen-patti-pro/game.js", "application/javascript"),
                            ("/teen-patti-pro/theme.json", "application/json")):
            st, got, body = self.get(path)
            self.assertEqual(st, 200, path)
            self.assertIn(ctype, got, path)
            self.assertTrue(body, path)

    def test_lobby_is_reachable(self):
        # It shipped unreachable: present in the client directory, referenced by
        # nothing, and absent from the API's hardcoded route list.
        st, _, body = self.get("/teen-patti-pro/lobby.html")
        self.assertEqual(st, 200)
        self.assertIn(b"DearLive", body)

    def test_client_traversal_is_refused(self):
        for evil in ("/teen-patti-pro/..%2f..%2f.env",
                     "/teen-patti-pro/../../.env",
                     "/teen-patti-pro/....//api.py"):
            st, _, body = self.get(evil)
            self.assertEqual(st, 404, evil)
            self.assertNotIn(b"GAME_ADMIN_KEYS", body, evil)

    def test_non_whitelisted_client_extensions_are_refused(self):
        # .py and .env live inside the client directory's parent; even if a
        # future file of the same name lands beside it, it is not served.
        for path in ("/teen-patti-pro/api.py", "/teen-patti-pro/.env"):
            st, _, _ = self.get(path)
            self.assertEqual(st, 404, path)

    def test_missing_client_file_is_404(self):
        st, _, _ = self.get("/teen-patti-pro/does-not-exist.html")
        self.assertEqual(st, 404)


if __name__ == "__main__":
    unittest.main()
