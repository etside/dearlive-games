"""Client pages must reference their own assets by absolute, mount-aware paths.

This is the bug that made the deployed game look catastrophically broken while
every backend test passed.

`index.html` loaded the game with `src="game.js"`. The game is mounted at
`/teen-patti-pro`, and the canonical URL has **no trailing slash**, so a
relative reference resolves against `/` and the browser requested
`/game.js` -- which the server does not serve. Result on the real production
URL, captured with Playwright against https://api.ura-dhura.com:

    404 https://api.ura-dhura.com/game.js
    canvas: 300x150        <- default <canvas> size, never sized by JS
    distinctColours: 1     <- nothing painted
    WEBSOCKET: NONE        <- new WebSocket() never reached
    connText: "connecting" <- never advanced

No JavaScript ran at all, so there was no exception, no stack trace, and no
hint that the page itself was at fault. Every symptom -- stuck on "connecting",
blank table, no socket -- looked like a WebSocket, session or round problem.
Three separate investigations chased the backend before a browser load showed
the 404. `demo.html` and `how-to-play.html` carried the same class of bug
(`href="index.html"`, `href="./"`).

A relative path is only safe when the URL ends in a slash, so it is correct on
exactly one of the two spellings of the same resource. These tests assert every
page-local reference is absolute, which is correct on both.
"""
import re
import unittest
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1] / "games/teen_patti_pro/client"
PAGES = ("index.html", "demo.html", "lobby.html", "how-to-play.html")
MOUNT = "/teen-patti-pro"


def _local_refs(html: str):
    """Yield (attr, url) for src/href that are not absolute and not anchors."""
    for attr, url in re.findall(r'\b(src|href)\s*=\s*"([^"]+)"', html):
        if url.startswith(("http://", "https://", "#", "data:", "//", "mailto:")):
            continue
        if url.startswith("/"):
            continue  # already absolute
        yield attr, url


class ClientPathTest(unittest.TestCase):
    def test_no_page_uses_a_relative_path(self):
        offenders = {}
        for page in PAGES:
            p = CLIENT / page
            if not p.is_file():
                continue
            bad = [f"{a}={u}" for a, u in _local_refs(p.read_text(encoding="utf-8"))
                   if not u.startswith("${")]  # ${...} are JS template strings
            if bad:
                offenders[page] = bad
        self.assertEqual(
            offenders, {},
            "relative src/href resolves against '/' on /teen-patti-pro and 404s; "
            f"use absolute paths under {MOUNT}: {offenders}")

    def test_game_script_is_loaded_from_the_mount(self):
        html = (CLIENT / "index.html").read_text(encoding="utf-8")
        m = re.search(r'<script[^>]+src="([^"]+)"', html)
        self.assertIsNotNone(m, "index.html loads no script")
        self.assertEqual(
            m.group(1), f"{MOUNT}/game.js",
            "the game script must be referenced absolutely, or it 404s and the "
            "page renders a blank canvas with no socket and no error")

    def test_every_local_reference_resolves_to_a_served_route(self):
        """Cross-check the references against the server's route table."""
        api = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        for page in PAGES:
            p = CLIENT / page
            if not p.is_file():
                continue
            for attr, url in _local_refs(p.read_text(encoding="utf-8")):
                if url.startswith("${"):
                    continue
                with self.subTest(page=page, url=url):
                    target = url.split("?")[0].split("#")[0]
                    self.assertTrue(
                        f'"{target}"' in api or f'"{target.lstrip("/")}"' in api
                        or f"{MOUNT}/{target.lstrip('/')}" in api,
                        f"{page} references {url}, which the server has no route for")


if __name__ == "__main__":
    unittest.main()
