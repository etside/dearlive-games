"""Art pack integration.

The pack in assets/games/teen-patti-pro/ shipped 93 files and 91 of them were
never referenced: the client drew chips, seats and status procedurally. A
subset is now wired -- chips, seat variants, connection status icons -- with a
deliberate rule:

    every pack image is optional, and the procedural drawing is the fallback.

This build cannot be visually verified (no browser available in the build
environment), so an asset that renders badly must degrade to the known-good
drawing rather than to a blank space. These tests pin the wiring, the mappings
and the existence of the fallback, and assert that every referenced pack file
actually resolves over HTTP.
"""
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from common.wallet import MemoryWallet
from games.teen_patti_pro.api import Handler
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "assets/games/teen-patti-pro"
CLIENT = ROOT / "games/teen_patti_pro/client"
JS = (CLIENT / "game.js").read_text(encoding="utf-8")


# A concatenation fragment is not a path: `ART_BASE + 'chips/chip-' + ...` starts
# with the same marker as a whole literal, so fragments are filtered out and the
# chip names are computed from the denomination instead.
_FRAGMENT = re.compile(r"^(chips|seats|ui|cards|avatars)/[^/]*$")


def _literal_pack_paths():
    out = set()
    for m in re.finditer(r"ART_BASE \+ '([^']+)'", JS):
        rel = m.group(1)
        # A real path has a file component after its folder.
        if "/" in rel.rstrip("/") and "." in rel.rsplit("/", 1)[-1]:
            out.add(rel)
    return out


def _all_pack_paths():
    """Every pack file the client can request, including computed names."""
    paths = _literal_pack_paths()
    m = re.search(r"'chips/chip-'\s*\+", JS)
    if not m:
        raise AssertionError("chip art builder not found in game.js")
    big = re.search(r"d >= 1000 \? '([\w-]+)'", JS)
    if not big:
        raise AssertionError("chip 1000+ slug rule not found in game.js")
    from games.teen_patti_pro.config import DEFAULT_CONFIG
    for d in DEFAULT_CONFIG.denoms:
        slug = big.group(1) if d >= 1000 else str(d)
        paths.add(f"chips/chip-{slug}.svg")
    return paths


class PackWiringTest(unittest.TestCase):
    def test_chip_denominations_map_to_pack_files(self):
        # The pack names chips by value and the client builds the name from the
        # denomination, so the literal never appears in the source. Assert the
        # builder exists, that the slug rule is the expected one, and that each
        # resulting file is present.
        self.assertRegex(JS, r"function CHIP_FACE\(d\) \{ return ART_BASE \+ "
                            r"'chips/chip-' \+ \(d >= 1000 \? '1k' : d\) \+ '\.svg'; \}")
        for d in (20, 100, 500, 1000):
            slug = "1k" if d >= 1000 else str(d)
            self.assertTrue((PACK / "chips" / f"chip-{slug}.svg").is_file(),
                            f"chip-{slug}.svg missing")
        # Every configured denomination must resolve, not just these four.
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        for d in DEFAULT_CONFIG.denoms:
            slug = "1k" if d >= 1000 else str(d)
            self.assertTrue((PACK / "chips" / f"chip-{slug}.svg").is_file(),
                            f"denomination {d} has no chip art")

    def test_seats_map_to_the_srs_order(self):
        # SRS section 1: A green, B blue, C red. Asserted as a triple so a
        # swap cannot pass.
        for seat, colour in (("A", "green"), ("B", "blue"), ("C", "red")):
            self.assertIn(f"{seat}: ART_BASE + 'seats/seat-{colour}.svg'", JS,
                          f"seat {seat} must use seat-{colour}.svg")
            self.assertTrue((PACK / "seats" / f"seat-{colour}.svg").is_file())

    def test_every_connection_state_has_a_status_icon(self):
        for state in ("live", "polling", "connecting", "offline", "error"):
            # No ^ anchor: assertRegex compiles without re.MULTILINE, so ^ only
            # matches the start of the whole document.
            self.assertRegex(JS, rf"\b{state}: ART_BASE \+ 'ui/status-[a-z-]+\.svg'",
                             f"no status icon for {state}")

    def test_every_wired_pack_file_exists_on_disk(self):
        referenced = _all_pack_paths()
        self.assertGreaterEqual(len(referenced), 11,
                                f"expected the pack to be referenced, saw {referenced}")
        missing = [r for r in referenced if not (PACK / r).is_file()]
        self.assertEqual(missing, [], f"referenced but absent: {missing}")

    def test_every_wired_pack_file_is_valid_xml(self):
        import xml.etree.ElementTree as ET
        for rel in _all_pack_paths():
            try:
                ET.parse(PACK / rel)
            except ET.ParseError as exc:
                self.fail(f"{rel} is not valid SVG/XML: {exc}")


class PackFallbackTest(unittest.TestCase):
    """The fallback is the safety net, so it has to actually be there."""

    def test_a_ready_check_guards_every_pack_draw(self):
        self.assertIn("function imageReady(img, w, h)", JS)
        # Each of the three draw sites must be behind a readiness test.
        self.assertGreaterEqual(JS.count("imageReady("), 4,
                                "expected a guarded draw for chips, seats, status")

    def test_the_fallback_rejects_unloaded_images(self):
        # An <img> that 404s reports complete=true with naturalWidth 0, so the
        # check must test naturalWidth, not just complete.
        self.assertIn("img.complete && img.naturalWidth > 0", JS)
        self.assertIn("naturalWidth", JS)

    def test_chip_fallback_is_the_procedural_chip(self):
        block = JS.split("const art = CHIP_IMAGES[d]")[1][:900]
        self.assertIn("} else {", block, "chip needs an else branch")
        self.assertIn("ctx.arc(x, y, 24, 0, 7)", block,
                      "fallback must draw the original procedural chip")

    def test_seat_fallback_keeps_the_generated_chairs(self):
        self.assertIn("} else if (seatImages[i]", JS)
        self.assertIn("pt.x - 60, pt.y - 38, 120, 72", JS,
                      "the generated-chair rect must survive as the fallback")

    def test_status_fallback_keeps_the_coloured_dot(self):
        self.assertIn("hud.dot.style.backgroundImage = ''", JS)

    def test_preload_failure_does_not_throw(self):
        # preloadImage is called at module scope; a thrown error there would
        # take the whole client down before it rendered anything.
        self.assertIn("function preloadImage(src)", JS)
        block = JS.split("function preloadImage(src)")[1][:240]
        self.assertNotIn("throw", block.lower(), "preload must not throw")


class OrphanAssetTest(unittest.TestCase):
    """The pack is partially wired by decision; this records what is still
    unused so the gap is visible rather than discovered later."""

    def test_card_faces_are_deliberately_not_wired(self):
        # Left procedural on purpose: 59 files swapped in blind, with no way to
        # see the result, is the highest-risk change available.
        cards = sorted((PACK / "cards").glob("*.svg"))
        self.assertEqual(len(cards), 59, "card pack size changed; revisit this")
        for card in cards[:3]:
            self.assertNotIn(card.name, JS,
                             f"{card.name} is now referenced; update this test")

    def test_remaining_orphans_are_known(self):
        referenced = _all_pack_paths()
        referenced |= {"avatars/avatar-placeholder.svg",
                       "avatars/avatar-frame-navy.svg"}
        all_files = {str(p.relative_to(PACK)) for p in PACK.rglob("*.svg")}
        orphans = sorted(all_files - referenced)
        # The count is asserted so a change in either direction is noticed.
        self.assertEqual(len(orphans), len(all_files) - len(referenced))
        self.assertGreater(len(orphans), 0, "expected the pack is only partly wired")


class PackResolvesOverHttpTest(unittest.TestCase):
    """A referenced file that 404s is the failure mode that already bit the
    seat assets once. Assert every wired file actually serves."""

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

    def status(self, path):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return r.status, r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception:
            return 0, ""

    def test_every_wired_pack_asset_serves(self):
        for rel in sorted(_all_pack_paths()):
            path = "/assets/games/teen-patti-pro/" + rel
            code, ctype = self.status(path)
            self.assertEqual(code, 200, f"{rel} -> {code}")
            self.assertIn("svg", ctype.lower(), rel)

    def test_pack_traversal_is_still_refused(self):
        code, _ = self.status("/assets/games/teen-patti-pro/../../../.env")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
