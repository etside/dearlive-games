"""GET /api-docs serves the player integration docs page."""
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
