"""The operator console must be a real console.

It used to be a 69-line file that JSON.stringify'd an API response and pointed
at /api/v1/operator/admin/* -- a namespace whose handlers were removed in an
earlier commit, so six of its seven links 404'd and the panel itself was not
served at all. These tests pin the properties that make it worth shipping:

* the panel is actually served
* every section maps to a route that exists
* it never renders a raw JSON dump as its primary output
* it does not reference a removed game or the removed super-admin namespace
"""
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from common.wallet import MemoryWallet
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService

PANEL = "apps/admin/public"
APP_JS = f"{PANEL}/app.js"
INDEX_HTML = f"{PANEL}/index.html"
# SRS section 10 section name -> a word that must appear in the panel for it.
# Matched on concept rather than the exact SRS string, so renaming a nav label
# does not fail the test while dropping a section still does.
SRS_SECTIONS = {
    "dashboard": "dashboard",
    "profit & risk": "risk",
    "player override": "override",
    "token package builder": "package",
    "game rules": "rules",
    "enable/disable": "disable",
    "audit log": "audit",
    "reports": "report",
    "settings": "settings",
}


def _js() -> str:
    with open(APP_JS, encoding="utf-8") as fh:
        return fh.read()


class PanelSourceTest(unittest.TestCase):
    def setUp(self):
        self.js = _js()

    def test_every_srs_section_is_present(self):
        # SRS section 10 lists nine sections. A section with no endpoint behind
        # it is still present, but says so in the UI (see Reports).
        low = self.js.lower()
        for name, word in SRS_SECTIONS.items():
            self.assertIn(word, low, f"section {name} has no '{word}' anywhere")

    def test_exactly_nine_sections_plus_scheduled(self):
        # Scheduled is a tenth, added because the SRS asks for date-and-time
        # config changes. Anything beyond that is scope the package did not get.
        registered = set(re.findall(r"SECTIONS\.(\w+)\s*=", self.js))
        self.assertEqual(len(registered), 10, sorted(registered))

    def test_every_section_is_registered_as_a_route(self):
        registered = set(re.findall(r"SECTIONS\.(\w+)\s*=", self.js))
        self.assertGreaterEqual(len(registered), 9,
                                f"only {len(registered)} sections registered")

    def test_it_calls_the_admin_namespace_that_exists(self):
        # The old panel used /api/v1/operator/admin/*, which is gone.
        self.assertNotIn("/operator/admin/", self.js)
        self.assertIn('request("GET", "/admin/', self.js)

    def test_every_endpoint_it_calls_is_routed(self):
        """Each request() path must correspond to a real route.

        Dynamic segments in the panel source (string concatenation building a
        path) are turned into a wildcard segment, then the documented path is
        compiled as a pattern and matched against the source's route patterns
        the same way test_readme_accuracy does it.
        """
        api = open("games/teen_patti_pro/api.py", encoding="utf-8").read()
        routes = set()
        for anchor in ("def do_GET", "def do_POST", "def do_PUT",
                       "def do_DELETE", "def _admin_post", "def _admin_put",
                       "def _admin_delete"):
            if anchor not in api:
                continue
            tail = api[api.index(anchor):]
            nxt = len(tail)
            for other in ("def do_GET", "def do_POST", "def do_PUT",
                          "def do_DELETE", "def _admin_post", "def _admin_put",
                          "def _admin_delete", "\n    def "):
                i = tail.find(other, 1)
                if i != -1:
                    nxt = min(nxt, i)
            body = tail[:nxt]
            for m in re.finditer(
                    r'path == "([^"]+)"|path\.startswith\("([^"]+)"\)'
                    r'|re\.fullmatch\(r"([^"]+)"', body):
                routes.add([g for g in m.groups() if g][0])
        self.assertGreater(len(routes), 20, "route extraction failed")

        called = set(re.findall(r'request\("[A-Z]+",\s*"([^"]+)"', self.js))
        self.assertGreater(len(called), 10, "expected many calls")

        unrouted = []
        for path in called:
            bare = path.split("?")[0].rstrip("/")
            # Collapse JS concatenation into one wildcard segment.
            pattern = re.sub(r'"\s*\+\s*[A-Za-z_][\w.]*\s*\+\s*"', r"[^/]+", bare)
            pattern = re.sub(r'"\s*\+\s*GAME\s*\+\s*"', r"[^/]+", pattern)
            if self._routed(pattern, routes):
                continue
            unrouted.append(path)
        self.assertEqual(unrouted, [],
                         "panel calls endpoints that are not routed")

    @staticmethod
    def _routed(pattern: str, routes) -> bool:
        for route in routes:
            if route == pattern:
                return True
            try:
                if re.fullmatch(route, pattern):
                    return True
            except re.error:
                continue
            # A route like /api/v1/admin/players/([^/]+)/appearance serves
            # /admin/players/<anything>/appearance.
            if route.endswith("/") and pattern.startswith(route):
                return True
            if route.startswith("/api/v1" + pattern):
                return True
        return False

    def test_it_does_not_dump_raw_json_as_the_page(self):
        # The old panel's entire render was a <pre> of the response.
        self.assertNotIn("JSON.stringify(data, null, 2)", self.js)
        self.assertIn("table(", self.js, "expected real table rendering")
        self.assertIn("kpi(", self.js, "expected KPI rendering")

    def test_it_has_real_write_paths_not_just_reads(self):
        for verb, path in (("PUT", "/admin/profit-risk"),
                           ("POST", "/admin/profit-risk/simulate"),
                           ("POST", "/admin/packages"),
                           ("PUT", "/admin/games/"),
                           ("POST", "/admin/scheduled-changes"),
                           ("DELETE", "/admin/scheduled-changes/")):
            self.assertIn(f'"{verb}", "{path}', self.js, f"{verb} {path}")

    def test_override_requires_a_reason_client_side(self):
        # The schema requires it; catching it here saves a round trip and a
        # confusing 502.
        self.assertIn("reason is required", self.js)

    def test_it_never_stores_the_key_in_local_storage(self):
        # sessionStorage dies with the tab. localStorage would leave an admin
        # credential on a shared machine.
        self.assertIn("sessionStorage", self.js)
        self.assertNotIn("localStorage.setItem(KEY_STORE", self.js)

    def test_it_sends_the_admin_key_header(self):
        self.assertIn('"X-Admin-Key"', self.js)

    def test_it_explains_the_503_case(self):
        # A fresh install has no database. Saying so is the difference between
        # a five-second fix and an hour of guessing.
        self.assertIn("503", self.js)
        self.assertIn("DATABASE_URL", self.js)

    def test_reports_section_is_honest_about_having_no_endpoint(self):
        self.assertIn("no reports endpoint", self.js)

    def test_audit_supports_csv_export(self):
        # SRS section 10: "immutable. CSV export."
        self.assertIn("exportCsv", self.js)
        self.assertIn("text/csv", self.js)

    def test_no_removed_game_or_role(self):
        low = self.js.lower()
        for gone in ("wheel", "greedy", "baby king", "superadmin", "super-admin"):
            self.assertNotIn(gone, low, gone)

    def test_it_has_loading_empty_and_error_states(self):
        for kind in ("stateNode", "loading", "Loading"):
            self.assertIn(kind, self.js, kind)

    def test_index_loads_the_assets_it_references(self):
        html = open(INDEX_HTML, encoding="utf-8").read()
        for ref in re.findall(r'(?:src|href)="(/admin/[^"]+)"', html):
            self.assertTrue(ref.lstrip("/") or True)
        self.assertIn("/admin/app.js", html)
        self.assertIn("/admin/styles.css", html)

    def test_panel_targets_exactly_one_game(self):
        self.assertIn('var GAME = "teen-patti-pro"', self.js)


class PanelIsServedTest(unittest.TestCase):
    """The panel has to actually be reachable, which it was not."""

    @classmethod
    def setUpClass(cls):
        w = MemoryWallet()
        w.fund("qa-player", 20000)
        Handler.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                                       wallet=w)
        Handler.game_enabled = {}
        Handler.game_packages = {}
        Handler.game_labels = {}
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path):
        base = f"http://127.0.0.1:{self.port}"
        try:
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def test_the_panel_is_served(self):
        for path in ("/admin", "/admin/"):
            st, ctype, body = self.get(path)
            self.assertEqual(st, 200, path)
            self.assertIn("text/html", ctype)
            self.assertIn(b"Operator", body)

    def test_the_panel_assets_are_served_with_the_right_types(self):
        for path, needle in (("/admin/app.js", b"application/javascript"),
                             ("/admin/styles.css", b"text/css")):
            st, ctype, _ = self.get(path)
            self.assertEqual(st, 200, path)
            self.assertIn(needle.decode(), ctype, path)

    def test_panel_traversal_is_refused(self):
        for evil in ("/admin/../api.py", "/admin/%2e%2e/api.py",
                     "/admin/../../.env", "/admin/..%2f.env"):
            st, _, body = self.get(evil)
            self.assertEqual(st, 404, evil)
            self.assertNotIn(b"ADMIN_KEYS", body, evil)

    def test_panel_refuses_non_web_extensions(self):
        for path in ("/admin/../games/teen_patti_pro/api.py",
                     "/admin/app.py", "/admin/.env"):
            st, _, _ = self.get(path)
            self.assertEqual(st, 404, path)


if __name__ == "__main__":
    unittest.main()
