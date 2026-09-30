"""GET /api-docs serves the player integration docs page."""
import os
import re
import sys
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService


class ApiDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.svc = TeenPattiService(config=TeenPattiConfig(confirmed=True),
                                       wallet=MemoryWallet())
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _get(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def test_api_docs_serves_html_200(self):
        st, ctype, body = self._get("/api-docs")
        self.assertEqual(st, 200, body[:200])
        self.assertIn("text/html", ctype)
        text = body.decode("utf-8", "replace")
        self.assertIn("Teen Patti Pro", text)
        self.assertIn("copy", text.lower())

    def test_api_docs_covers_the_contract(self):
        _, _, body = self._get("/api-docs")
        text = body.decode("utf-8", "replace")
        for section in ("Authentication", "Demo URL", "Production URL",
                        "Query Parameters", "Endpoints", "Response Envelope",
                        "Mint Session", "Place Bet", "WebSocket Events",
                        "Integration", "Support"):
            self.assertIn(section, text, f"missing section: {section}")
        # Wire-accurate details the page must not drift from.
        for token in ("launch_url", "Idempotency-Key", "X-API-Key",
                      "X-Signature", "round.created", "betting.closed",
                      "result.declared", "settlement.completed",
                      "WALLET_UNAVAILABLE", "serverTimeMs", "requestId",
                      "demo_token", "/api/v1/sessions",
                      "/api/v1/games/teen-patti-pro/rooms/"):
            self.assertIn(token, text, f"missing contract detail: {token}")

    def test_api_docs_is_not_confused_with_game_routes(self):
        st, _, _ = self._get("/api-docs/extra-path")
        self.assertEqual(st, 404)


if __name__ == "__main__":
    unittest.main()


class TestCanonicalGameCodeAccepted(unittest.TestCase):
    """Provider-minted tokens carry game_code teen_patti_pro (TEEN_CODE).

    game_service() only accepted {"teen-patti-pro", "teen_patti"}, so every
    Bearer gst_* request to a game_service() route -- including
    GET /api/v1/wallet/balance -- 404'd as "Unknown game" and the client
    showed a null balance on a funded wallet.
    """

    def test_teen_ids_includes_canonical_provider_code(self):
        from games.teen_patti_pro import api as api_mod
        from provider.games import TEEN_CODE
        self.assertIn(TEEN_CODE, api_mod.Handler.TEEN_IDS)

    def test_game_service_resolves_canonical_code(self):
        from games.teen_patti_pro import api as api_mod
        from provider.games import TEEN_CODE
        self.assertEqual(
            api_mod.Handler.game_kind(api_mod.Handler, TEEN_CODE), "teen")


class IntegrationGuidesTest(unittest.TestCase):
    """The docs must cover the five integrations the package promised.

    These were specified and absent. Each guide is checked for the specific
    thing that would make it useless if left out -- a code block that does not
    compile, or a "here is the shape" with no shape.
    """

    HTML = (Path(__file__).resolve().parents[1]
            / "apps" / "player" / "public" / "api-docs.html").read_text(
                encoding="utf-8")

    def test_every_guide_has_an_anchor_and_a_heading(self):
        for anchor in ("flutter", "nestjs", "mongodb", "agora", "nextjs",
                       "install", "errors", "limits"):
            self.assertIn('id="%s"' % anchor, self.HTML,
                          "no section for #%s" % anchor)
            self.assertIn('href="#%s"' % anchor, self.HTML,
                          "#%s is not linked from the table of contents" % anchor)

    def test_guides_carry_real_code_not_prose(self):
        for anchor in ("flutter", "nestjs", "mongodb", "nextjs"):
            i = self.HTML.index('id="%s"' % anchor)
            j = self.HTML.find("<section", i + 10)
            block = self.HTML[i:j if j > 0 else len(self.HTML)]
            self.assertIn("<pre>", block,
                          "#%s has no code sample" % anchor)

    def test_flutter_guide_does_not_put_the_hmac_secret_in_the_app(self):
        i = self.HTML.index('id="flutter"')
        block = self.HTML[i:self.HTML.find("<section", i + 10)]
        # The secret belongs in the operator's backend. A guide that shows a
        # Flutter client calling POST /api/v1/sessions teaches people to ship
        # the operator's HMAC secret inside an app bundle.
        self.assertNotIn("OPERATOR_HMAC_SECRET", block)
        self.assertIn("launch", block.lower())

    def test_nestjs_guide_warns_about_idempotency_and_signature(self):
        i = self.HTML.index('id="nestjs"')
        block = self.HTML[i:self.HTML.find("<section", i + 10)]
        self.assertIn("idempotent", block.lower())
        self.assertIn("timingSafeEqual", block)
        self.assertIn("nonce", block.lower())

    def test_nestjs_guide_lists_all_four_wallet_operations(self):
        i = self.HTML.index('id="nestjs"')
        block = self.HTML[i:self.HTML.find("<section", i + 10)]
        for op in ("balance", "debit", "credit", "rollback"):
            self.assertIn("<code>%s</code>" % op, block,
                          "the %s callback is undocumented" % op)

    def test_mongodb_guide_states_there_is_no_direct_access(self):
        i = self.HTML.index('id="mongodb"')
        block = self.HTML[i:self.HTML.find("<section", i + 10)]
        self.assertIn("no direct database access", block.lower())
        # The unique settlement index is the whole point of storing a copy.
        self.assertIn("db.settlements.createIndex", block)
        self.assertIn("unique: true", block)

    def test_nextjs_guide_replaces_the_admin_key(self):
        i = self.HTML.index('id="nextjs"')
        block = self.HTML[i:self.HTML.find("<section", i + 10)]
        self.assertIn("X-Admin-Key", block)
        self.assertIn("ALLOW", block,
                      "the proxy needs a path allowlist, not a pass-through")
        self.assertIn("do not forward", block.lower())

    def test_error_table_matches_the_codes_the_server_returns(self):
        # Every E_* constant in common/envelope.py should appear in the table.
        env = (Path(__file__).resolve().parents[1]
               / "common" / "envelope.py").read_text(encoding="utf-8")
        import re as _re
        codes = set(_re.findall(r'E_\w+\s*=\s*"([A-Z_]+)"', env))
        self.assertGreaterEqual(len(codes), 10)
        for code in codes:
            self.assertIn("<code>%s</code>" % code, self.HTML,
                          "error %s is returned by the server but not "
                          "documented" % code)

    def test_rate_limits_match_the_enforced_number(self):
        api = (Path(__file__).resolve().parents[1]
               / "games" / "teen_patti_pro" / "api.py").read_text(encoding="utf-8")
        limit = re.search(r"_MINT_RATE_LIMIT\s*=\s*(\d+)", api)
        self.assertIsNotNone(limit, "cannot find the enforced mint limit")
        i = self.HTML.index('id="limits"')
        block = self.HTML[i:self.HTML.find("<section", i + 10)]
        self.assertIn("<td>%s</td>" % limit.group(1), block,
                      "the documented limit is not the enforced one (%s)"
                      % limit.group(1))

    def test_install_section_points_at_the_real_route(self):
        self.assertIn("/install", self.HTML)
        installer = (Path(__file__).resolve().parents[1]
                     / "scripts" / "install.sh")
        self.assertTrue(installer.is_file(), "the docs link to a missing script")
        self.assertIn("curl -fsSL https://api.ura-dhura.com/install | bash",
                      self.HTML)

    def test_the_copy_button_wrapper_is_positioned(self):
        # button.copy is position:absolute. A static wrapper anchors it to the
        # page, so the button lands nowhere near the code it copies.
        self.assertRegex(self.HTML, r"\.code\{position:relative")


class DocsMatchTheWireTest(unittest.TestCase):
    """The documented events must be exactly the emitted events.

    The events table once listed round.opened, round.updated and round.closed,
    of which the server emits none: an integration listening for those names
    hears nothing, forever, with no error to explain why. Conversely, real
    events that are not documented (bet.accepted, betting.opened,
    result.declared) leave an integrator guessing at the contract.

    So this asserts both directions against common/webhooks.py, which is the
    allowlist: every allowlisted event must appear in the docs, and every
    dotted event name the docs claim must be allowlisted.
    """

    @classmethod
    def setUpClass(cls):
        import ast as _ast
        with open(str(Path(__file__).resolve().parents[1] / "common" / "webhooks.py"), encoding="utf-8") as fh:
            tree = _ast.parse(fh.read())
        ev = next(n for n in _ast.walk(tree)
                  if isinstance(n, _ast.Assign)
                  and getattr(n.targets[0], "id", "") == "EVENTS")
        cls.allowlist = set(_ast.literal_eval(ev.value))
        with open(str(Path(__file__).resolve().parents[1] / "apps" / "player" / "public" / "api-docs.html"),
                  encoding="utf-8") as fh:
            cls.docs = fh.read()

    def test_every_allowlisted_event_is_documented(self):
        import re
        documented = set(re.findall(r"<code>([a-z][a-z0-9_.]+)</code>", self.docs))
        missing = {e for e in self.allowlist if e not in documented}
        self.assertEqual(missing, set(),
                         "emitted but undocumented events: %s" % sorted(missing))

    def test_no_documented_event_name_is_fictional(self):
        """A documented name that is not on the wire is the worse failure.

        Scoped to the WebSocket section, because that is where event names
        live: elsewhere in the doc a dotted lowercase token is a field path
        like `data.code`, not an event. The deliberate callouts that say
        `round.opened` and friends were removed are matched by name and
        excluded, since their whole point is to warn an integrator off them.
        """
        import re
        sec = re.search(r'<section id="ws">(.*?)</section>', self.docs, re.S)
        self.assertIsNotNone(sec, "no WebSocket section in the docs")
        body = sec.group(1)
        body = re.sub(r"\(No <code>round\.opened</code> exists.*?\)", "", body)
        documented = set(re.findall(r"<code>([a-z][a-z0-9_.]+)</code>", body))
        fictional = {d for d in documented
                     if "." in d
                     and d not in self.allowlist
                     and d not in ("error",)
                     and not d.startswith("data.")
                     # The doc explicitly warns these three off; that warning
                     # is the whole point of naming them.
                     and d not in ("round.opened", "round.updated",
                                   "round.closed")}
        self.assertEqual(fictional, set(),
                         "documented but never emitted: %s" % sorted(fictional))
