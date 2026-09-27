"""The client must always resolve a usable WebSocket URL.

Regression: `https://api.ura-dhura.com/teen-patti-pro` rendered the game page
but sat on "connecting" forever. `game.js` resolved the WS endpoint as

    (q.get('ws') || window.DL_WEBSOCKET_URL || '')

`DL_WEBSOCKET_URL` was only ever *read*, never defined anywhere in the repo, so
a bare launch URL produced the empty string. `new WebSocket('')` throws, the
throw was swallowed into a silent fallback to REST polling, and the connection
badge never left "connecting". The game was unplayable while 634 tests passed.

`tests/test_polling_fallback.py` asserted only that the string
"window.DL_WEBSOCKET_URL" appears in the source -- which it still did. Asserting
on the presence of an identifier is not the same as asserting the value it
produces is usable. This module evaluates the real expression from the real file
under Node, for every precedence case, and fails if any of them yields a URL
that cannot be connected to.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

CLIENT_JS = Path(__file__).resolve().parents[1] / (
    "games/teen_patti_pro/client/game.js")


def extract_ws_resolution(source: str) -> str:
    """Pull the WS resolution expression out of game.js.

    Returns the JS source of the initialiser, so the test exercises the shipped
    code rather than a copy of it that can drift.
    """
    m = re.search(r"const WS_RAW\s*=\s*(.*?);\s*\n\s*const WS\s*=\s*(.*?);",
                  source, re.S)
    assert m, "could not find WS resolution in game.js -- update this test"
    return "const WS_RAW = %s; const WS = %s;" % (m.group(1), m.group(2))


@unittest.skipUnless(shutil.which("node") or shutil.which("nodejs"),
                     "node not available to evaluate client JS")
class WebSocketUrlResolutionTest(unittest.TestCase):
    """Evaluate the shipped expression under Node for each precedence case."""

    def _resolve(self, query: str = "", injected: str = None,
                 protocol: str = "https:", host: str = "api.ura-dhura.com"):
        node = shutil.which("node") or shutil.which("nodejs")
        expr = extract_ws_resolution(CLIENT_JS.read_text(encoding="utf-8"))
        parts = [
            "const location = {protocol: %r, host: %r, search: %r};"
            % (protocol, host, query),
            "const window = {};",
        ]
        if injected:
            parts.append("window.DL_WEBSOCKET_URL = %s;" % json.dumps(injected))
        parts += [
            "const q = new URLSearchParams(location.search);",
            expr,
            "console.log(WS);",
        ]
        out = subprocess.run([node, "-e", "\n".join(parts)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0,
                         f"evaluating WS resolution failed: {out.stderr}")
        return out.stdout.strip()

    def test_bare_url_no_params_resolves(self):
        """The real-world failure: no ?ws=, no injected global."""
        ws = self._resolve()
        self.assertTrue(ws, "WS resolved to empty string")
        self.assertTrue(ws.startswith("wss://"),
                        f"https page must default to wss://, got {ws!r}")
        self.assertIn("api.ura-dhura.com", ws)

    def test_same_origin_default_includes_ws_path(self):
        ws = self._resolve()
        self.assertEqual(ws, "wss://api.ura-dhura.com/ws")

    def test_plain_http_falls_back_to_ws_scheme(self):
        ws = self._resolve(protocol="http:", host="localhost:8000")
        self.assertTrue(ws.startswith("ws://"), ws)
        self.assertNotIn("wss://", ws, "http page must not request wss://")

    def test_query_parameter_wins(self):
        ws = self._resolve(query="?ws=wss://ws.ura-dhura.com")
        self.assertEqual(ws, "wss://ws.ura-dhura.com")

    def test_injected_global_is_used_when_no_query(self):
        ws = self._resolve(injected="wss://injected.example.com")
        self.assertEqual(ws, "wss://injected.example.com")

    def test_query_parameter_beats_injected_global(self):
        ws = self._resolve(query="?ws=wss://from-query.example.com",
                           injected="wss://from-global.example.com")
        self.assertEqual(ws, "wss://from-query.example.com")

    def test_trailing_slash_is_stripped(self):
        ws = self._resolve(query="?ws=wss://ws.ura-dhura.com/")
        self.assertEqual(ws, "wss://ws.ura-dhura.com")

    def test_never_resolves_to_empty_for_any_input(self):
        """The invariant that was violated in production."""
        cases = [
            dict(), dict(query="?ws="), dict(injected=""),
            dict(query="?ws=&api=https://x.example.com"),
            dict(query="?other=1"),
        ]
        for kwargs in cases:
            with self.subTest(**kwargs):
                ws = self._resolve(**kwargs)
                self.assertTrue(ws, f"WS empty for {kwargs}")

    def test_client_still_builds_a_websocket(self):
        """The resolution change must not have removed the socket itself."""
        source = CLIENT_JS.read_text(encoding="utf-8")
        self.assertIn("new WebSocket(", source)
        self.assertIn("ws.onopen", source,
                      "client must still react to a successful handshake")


if __name__ == "__main__":
    unittest.main()
