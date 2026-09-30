"""The player home page must exist, and must not lie about the server.

Two faults this file exists to prevent, both of which shipped:

1. `/` returned 404 with a JSON body. The markup in apps/player/public was
   complete and correct; nothing served it. There was no route for `/`,
   `/app.js` or `/styles.css`, so the front door of the site did not exist and
   the navbar's own brand link was a dead end.

2. The page was a staging QA console wired to three routes that do not exist
   (POST /api/v1/staging/test-login, /api/v1/staging/test-wallet/grant,
   POST /api/v1/games/{id}/sessions). Every control 404'd. It also carried
   three copies of the navbar, from a text substitution that ran more than
   once over the same file.

The rule these tests encode: the home page may only call endpoints that exist,
and it may not ship a control that cannot work.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "apps" / "player" / "public" / "index.html"
APP_JS = ROOT / "apps" / "player" / "public" / "app.js"
API = ROOT / "games" / "teen_patti_pro" / "api.py"
CSS = ROOT / "apps" / "player" / "public" / "styles.css"

# Endpoints the home page is allowed to call, and why each one is real.
ALLOWED_CALLS = {
    "/health": "public liveness",
    "/api/v1/games/teen-patti-pro": "public table descriptor",
}


class HomePageExistsTest(unittest.TestCase):
    def test_index_html_is_present_and_non_trivial(self):
        self.assertTrue(HOME.is_file())
        self.assertGreater(len(HOME.read_text(encoding="utf-8")), 500)

    def test_every_asset_the_page_links_is_in_the_allowlist(self):
        api_src = API.read_text(encoding="utf-8")
        for asset in ("index.html", "app.js", "styles.css"):
            self.assertIn('"%s"' % asset, api_src,
                          "no route serves /%s" % asset)

    def test_root_is_routed(self):
        api_src = API.read_text(encoding="utf-8")
        self.assertRegex(
            api_src, r'path == "/"',
            "the home page has no route, so / returns 404")

    def test_player_assets_get_the_right_content_type(self):
        api_src = API.read_text(encoding="utf-8")
        # A hardcoded text/html would hand the browser app.js labelled as HTML;
        # a strict MIME check refuses to run it and the page has no behaviour.
        self.assertIn("PLAYER_MIME", api_src)
        for mime in ("text/html", "application/javascript", "text/css"):
            self.assertIn(mime, api_src)


class HomePageTruthfulnessTest(unittest.TestCase):
    """No control that cannot work, and no value that is not read."""

    def test_the_navbar_appears_exactly_once(self):
        html = HOME.read_text(encoding="utf-8")
        self.assertEqual(
            html.count('class="navbar"'), 1,
            "the navbar was inserted three times by a repeated substitution")

    def test_no_endpoint_that_does_not_exist_is_called(self):
        js = APP_JS.read_text(encoding="utf-8")
        called = set(re.findall(r"api\('([^']+)'\)", js))
        for path in called:
            self.assertIn(
                path, ALLOWED_CALLS,
                "the home page calls %s, which has no route. Either add the "
                "route or stop calling it: a button that 404s is worse than "
                "no button." % path)

    @staticmethod
    def _strip_comments(text):
        """Remove /* */ and // comments.

        The page documents which routes it stopped calling, and a substring
        search over the raw file would match its own explanation. What matters
        is that no live code path reaches a dead route.
        """
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"^\s*//.*$", "", text, flags=re.M)

    def test_no_removed_staging_flow_is_referenced(self):
        blob = self._strip_comments(
            HOME.read_text(encoding="utf-8") + "\n"
            + APP_JS.read_text(encoding="utf-8"))
        for dead in ("test-login", "test-wallet/grant", "launch_token",
                     "Idempotency-Key", "qa-player", "staging-room",
                     "session_id"):
            self.assertNotIn(dead, blob,
                             "the home page still references %s in live code"
                             % dead)

    def test_no_hardcoded_result_values(self):
        """A page that shows a number must have fetched it."""
        js = APP_JS.read_text(encoding="utf-8")
        # Every value written into the DOM comes from a response field. The one
        # literal set is the yes/no rendering of a boolean, not a KPI.
        for literal in ("10000", "10,000", "250000", "3 rounds"):
            self.assertNotIn(literal, js,
                             "the home page shows a literal %s instead of a "
                             "value read from the server" % literal)

    def test_every_element_the_script_writes_to_exists_in_the_page(self):
        html = HOME.read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        for wanted in re.findall(r"set\('([^']+)'", APP_JS.read_text(
                encoding="utf-8")):
            self.assertIn(wanted, ids,
                          "app.js writes to #%s, which the page does not have"
                          % wanted)
        for wanted in re.findall(r"fail\('([^']+)'",
                                 APP_JS.read_text(encoding="utf-8")):
            self.assertIn(wanted, ids,
                          "app.js error-writes to #%s, which does not exist"
                          % wanted)

    def test_every_element_the_script_reads_exists_in_the_page(self):
        html = HOME.read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        for wanted in re.findall(r"\$\('([^']+)'\)",
                                 APP_JS.read_text(encoding="utf-8")):
            self.assertIn(wanted, ids,
                          "app.js reads #%s, which the page does not have"
                          % wanted)

    def test_the_stylesheet_defines_the_classes_the_page_uses(self):
        html = HOME.read_text(encoding="utf-8")
        css = CSS.read_text(encoding="utf-8")
        for cls in ("hero", "lede", "cta-row", "entry-list", "fine"):
            self.assertIn('class="%s' % cls, html)
            self.assertIn("." + cls, css,
                          "%s is used in the page but not styled" % cls)

    @staticmethod
    def _routes(api_src):
        """Every path template the router mentions, compiled to a matcher.

        Templates come in two shapes in this file: a plain quoted path
        ("/api/v1/admin/dashboard") and a regex (r"/teen-patti-pro/([A-Za-z0-9]...)"
        that captures a filename). Both are read from the source, so a link
        cannot outlive its route and a new route needs no change here.
        """
        out = []
        for lit in re.findall(r'"(/[^"]{0,120})"', api_src):
            if "(" in lit or "[" in lit:
                continue
            out.append(re.compile("^" + re.escape(lit) + "/?$"))
        for rx in re.findall(r'r"(/[^"]{3,160})"', api_src):
            try:
                out.append(re.compile("^" + rx + "$"))
            except re.error:
                continue
        return out

    def test_links_point_at_routes_that_exist(self):
        api_src = API.read_text(encoding="utf-8")
        html = HOME.read_text(encoding="utf-8")
        routes = self._routes(api_src)
        self.assertTrue(routes, "no routes parsed from api.py; the extractor "
                                "is broken and would pass anything")
        checked = 0
        for href in sorted(set(re.findall(r'href="(/[^"?#]*)', html))):
            if href.startswith("/assets/") or href.startswith("/shared/"):
                continue
            checked += 1
            self.assertTrue(
                any(rx.match(href) for rx in routes),
                "the home page links to %s, which no route serves" % href)
        self.assertGreater(checked, 2,
                           "expected the page to link somewhere real")

    def test_the_shared_navbar_assets_are_served(self):
        """The page's chrome depends on these; a 404 leaves it unstyled.

        navbar.html is deliberately NOT addressable -- it is an authoring
        fragment that pages include, not a page -- so only the two assets the
        browser fetches are asserted here.
        """
        api_src = API.read_text(encoding="utf-8")
        self.assertIn("serve_shared", api_src)
        for asset in ("navbar.css", "navbar.js"):
            self.assertTrue((ROOT / "apps" / "shared" / asset).is_file(),
                            "missing shared/%s" % asset)
            self.assertIn(asset, HOME.read_text(encoding="utf-8"),
                          "the page does not reference shared/%s" % asset)


if __name__ == "__main__":
    unittest.main()
