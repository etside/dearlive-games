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


class SingleToolbarTest(unittest.TestCase):
    """Exactly one header: the DOM toolbar, or the canvas. Never both.

    `index.html` renders the toolbar as real elements -- hBack, connPill,
    hSound, hHelp, hMenu. `draw()` *also* painted a title, a LIVE/POLLING/
    OFFLINE line, a status icon and a round number into the same band. A
    Playwright capture of production showed the canvas title colliding with the
    round pill and the Live indicator drawn on top of both, with the top-right
    controls clipped.

    Two layers drawing the same chrome cannot be fixed by nudging pixels: the
    canvas header has to go. This test fails if any of those strings returns to
    the canvas.
    """

    def _draw_source(self):
        js = (CLIENT / "game.js").read_text(encoding="utf-8")
        i = js.index("function draw(")
        body = js[i:]
        # Comments describe the removed header by name; strip them so this
        # checks what is painted, not what is documented.
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        body = re.sub(r"(?m)^\s*//.*$", "", body)
        return body

    def test_canvas_does_not_paint_a_second_header(self):
        body = self._draw_source()
        for banned in ("TEEN PATTI PRO", "POLLING", "OFFLINE"):
            self.assertNotIn(
                banned, body,
                f"draw() paints {banned!r}; the DOM toolbar already owns the "
                "header, and the two layers overlap")

    def test_dom_toolbar_still_present(self):
        html = (CLIENT / "index.html").read_text(encoding="utf-8")
        for el in ("hBack", "connPill", "connText", "hSound", "hHelp", "hMenu"):
            self.assertIn(f'id="{el}"', html, f"DOM toolbar element {el} missing")

    def test_layout_uses_a_single_grid_not_independent_h_anchors(self):
        """Felt, seats and action bar must share one band grid."""
        js = (CLIENT / "game.js").read_text(encoding="utf-8")
        i = js.index("function layout()")
        fn = js[i:js.index("\n  function rr(", i)]
        for band in ("header", "seats", "cards", "centre", "pot", "action"):
            self.assertIn(f"{band}:", fn, f"layout() is missing the {band} band")
        self.assertIn("const band = {", fn)
        self.assertNotIn("H * 0.4", fn,
                         "layout() must not anchor bands to viewport height")


class MasterAssetPathTest(unittest.TestCase):
    """Lottie / gif / wav art must resolve on the game's own mount.

    All four master-asset URLs were client-relative *and* mis-ordered:
    'master/teen-patti-pro/lottie/x.json' resolved against '/' to
    /master/teen-patti-pro/... , while the server serves
    /teen-patti-pro/master/<kind>/<file>. So every one 404'd -- a console error
    on each timer tick, invisible in play because the art is decorative, but it
    is a 404 and it is noise in the browser console.
    """
    def test_no_client_relative_master_paths(self):
        js = (CLIENT / "game.js").read_text(encoding="utf-8")
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        js = re.sub(r"(?m)^\s*//.*$", "", js)
        bad = re.findall(r"['\"](master/[^'\"]+)['\"]", js)
        self.assertEqual(bad, [],
                         f"client-relative master asset paths: {bad}")

    def test_master_base_is_absolute_and_correctly_ordered(self):
        js = (CLIENT / "game.js").read_text(encoding="utf-8")
        m = re.search(r"const MASTER_BASE\s*=\s*'([^']+)'", js)
        self.assertIsNotNone(m, "MASTER_BASE not defined")
        self.assertEqual(m.group(1), "/teen-patti-pro/master/")
        # segment order must be mount/kind/file, not kind/.../mount
        self.assertTrue(m.group(1).startswith("/teen-patti-pro/master/"))

    def test_server_serves_the_same_shape(self):
        api = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        self.assertIn(r"/teen-patti-pro/master/([a-z]+)/", api)


class TimerPulsePlaceholderTest(unittest.TestCase):
    """The countdown must not be painted over with a solid orange square.

    `timer_pulse.json` in the master asset pack is a placeholder: a single
    shape layer with solid fill [1, 0.25, 0.05, 1] (#FF400D) on a 512x512
    canvas. It is a square, not a ring.

    It was harmless while it 404'd. Fixing MASTER_BASE made it resolve, and it
    then rendered as a bright orange block sitting exactly where the countdown
    ring should be. The fix was not to hide it with CSS -- it is to stop drawing
    it, because the real art is avatars/timer-ring.svg plus the stroked progress
    arc already drawn in draw().
    """
    ASSET = Path(__file__).resolve().parents[1] / (
        "assets/dearlive-master/teen-patti-pro/lottie/timer_pulse.json")

    def test_placeholder_lottie_is_a_solid_square(self):
        import json
        d = json.loads(self.ASSET.read_text(encoding="utf-8"))
        layers = d.get("layers", [])
        self.assertEqual(len(layers), 1, "expected a single-layer placeholder")
        fills = [s.get("c") for l in layers for s in (l.get("shapes") or [])
                 if s.get("ty") == "fl"]
        self.assertTrue(fills, "no fill found")
        k = fills[0].get("k") if isinstance(fills[0], dict) else fills[0]
        if isinstance(k, list) and k and isinstance(k[0], list):
            rgb = k[0][:3]
            # r=1, g=0.25, b=0.05 -> #FF400D
            self.assertEqual([round(x, 2) for x in rgb], [1.0, 0.25, 0.05])
        self.assertEqual((d.get("w"), d.get("h")), (512, 512))

    def test_timer_no_longer_plays_the_placeholder(self):
        js = (CLIENT / "game.js").read_text(encoding="utf-8")
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        js = re.sub(r"(?m)^\s*//.*$", "", js)
        self.assertNotIn("playLottie('timer_pulse'", js,
                         "the countdown must not be painted over with the "
                         "solid-orange placeholder square")
        self.assertNotIn('playLottie("timer_pulse"', js)

    def test_countdown_still_uses_the_real_ring_asset(self):
        js = (CLIENT / "game.js").read_text(encoding="utf-8")
        self.assertIn("avatars/timer-ring.svg", js)
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "assets/games/teen-patti-pro/avatars"
                         / "timer-ring.svg").is_file())
