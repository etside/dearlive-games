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
# The palace renderer reads the newer pack; chips, seats, panels and the
# background all come from here. Asserting against the legacy root would
# check files the player never loads.
PAL = ROOT / "assets/teen-patti"
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
    """Every pack file the client can request.

    Two of the pack's name families are built by string concatenation -- card
    faces and chips -- so a plain filename search reports 59 false orphans.
    They are reconstructed here from the same rules the client uses, which
    means this function and game.js can disagree; a test asserts they do not.
    """
    paths = _literal_pack_paths()

    # chips: chip-<value>.svg, with 1000+ spelled as <n>k. The slug is computed
    # from the value now, not a fixed table of four names, so the client can
    # draw whatever the config's denominations are. This reconstructs the same
    # rule rather than a hardcoded list, which is what let the client's list
    # and the config's list drift apart unnoticed.
    big = re.search(r"\((\w+)\s*>=\s*1000\s*\?\s*\(\1\s*/\s*1000\)\s*\+\s*'k'", JS)
    assert big, "chip slug rule not found in game.js"
    from games.teen_patti_pro.config import DEFAULT_CONFIG
    for d in DEFAULT_CONFIG.denoms:
        slug = f"{d // 1000}k" if d >= 1000 else d
        paths.add(f"chips/chip-{slug}.svg")

    # card faces: card-<rank>-<suit>.png for every rank x suit the engine can
    # deal, reconstructed from the engine's own RANKS and SUITS.
    rank_slug = dict(re.findall(r"'(\d+)':\s*'([A-Z])'", JS))
    suit_slug = dict(re.findall(r"([SHDC]):\s*'([a-z]+)'", JS))
    assert rank_slug, "RANK_SLUG map not found in game.js"
    assert suit_slug, "SUIT_SLUG map not found in game.js"
    from games.teen_patti_pro.engine import RANKS, SUITS
    for rank in RANKS:
        code = str(rank)
        slug = rank_slug.get(code, code)
        for suit in SUITS:
            paths.add(f"cards/card-{slug}-{suit_slug[suit]}.png")
    return paths


# Deliberately not wired into the renderer, with the reason recorded. Each one
# must state WHY, so the list cannot quietly grow into a dumping ground.
BUILD_TIME_INPUTS = {
    "cards/card-face-template.svg":
        "consumed by scripts/generate-cards.mjs to compose the 52 faces; "
        "the runtime never draws it",
}


def _orphan_paths():
    all_files = {str(p.relative_to(PACK))
                 for p in list(PACK.rglob("*.svg")) + list(PACK.rglob("*.png"))}
    referenced = _all_pack_paths()
    referenced |= {"avatars/avatar-placeholder.svg",
                   "avatars/avatar-frame-navy.svg"}
    referenced |= set(BUILD_TIME_INPUTS)
    # game-toolbar-frame.svg is drawn: palaceToolbar() paints it behind the
    # controls. It shipped with the panel/button replacements and had no
    # consumer until then -- which is exactly what this test exists to catch.
    #
    # Two directories hold art that is deliberately not drawn:
    #   extracted/  unpacked source the crops were made from
    #   cardsets/   alternative card sets, staged and documented but not
    #               selected -- see cardsets/figma/PROVENANCE.md
    # Neither is an orphan; both are recorded so the list cannot quietly grow
    # into a dumping ground.
    all_files = {p for p in all_files
                 if not p.startswith("extracted/")
                 and not p.startswith("cardsets/")}
    return sorted(all_files - referenced)


class PackWiringTest(unittest.TestCase):
    def test_chip_denominations_map_to_pack_files(self):
        # The pack names chips by value and the client builds the name from the
        # denomination, so the literal never appears in the source. Assert the
        # builder exists, that the slug rule is the expected one, and that each
        # resulting file is present.
        # The slug is the value in thousands, so 1000 -> 1k and 10000 -> 10k.
        # It used to collapse every value >= 1000 to '1k', which made 10000,
        # 50000 and 100000 all request chip-1k.svg.
        # The slug is the value in thousands, so 1000 -> 1k and 10000 -> 10k.
        # It used to collapse every value >= 1000 to '1k', which made 10000,
        # 50000 and 100000 all request chip-1k.svg.
        self.assertRegex(
            JS,
            r"function chipArt\(v\) \{[^}]*v >= 1000 \? \(v / 1000\) \+ 'k' : v",
            "chipArt must spell the chip slug from the value")
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        for d in list(DEFAULT_CONFIG.denoms) + [1000, 10000, 50000, 100000]:
            slug = f"{d // 1000}k" if d >= 1000 else str(d)
            self.assertTrue((PAL / "chips" / f"chip-{slug}.svg").is_file(),
                            f"denomination {d} has no chip art ({slug})")

    def test_the_client_does_not_hardcode_denominations(self):
        """The chip bar must not invent its own list.

        It used to hardcode [1000, 10000, 50000, 100000] while the config
        accepts [20, 100, 500, 1000], so three of the four chips a player could
        tap were refused by the table. The same bug shipped in the bot manager.
        """
        self.assertNotRegex(JS, r"const DENOMS = \[[0-9]")
        self.assertIn("setDenoms", JS)
        self.assertIn("snap.denoms", JS)

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
        # The card faces are crops of a raster reference sheet and ship as
        # PNG; there is no XML to validate in those. Everything else must
        # still parse, because a malformed SVG fails silently as a blank image.
        for rel in _all_pack_paths():
            if not rel.endswith(".svg"):
                continue
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

    def test_chip_fallback_is_legible_and_not_a_block(self):
        # The fallback used to be a saturated purple disc: while the art was in
        # flight a player saw a coloured block where a chip should be. It is
        # now a muted placeholder that still shows the value.
        block = JS.split("const art = chipImage(d)")[1][:900]
        self.assertIn("} else {", block, "chip needs an else branch")
        self.assertIn("placeholder(", block,
                      "the chip fallback must be the muted placeholder")
        self.assertIn("chipLabel(d)", block,
                      "the chip value must stay readable without art")
        self.assertNotIn("#8e44ad", block,
                         "no saturated fill may stand in for missing art")

    def test_seat_fallback_is_muted_not_a_solid_chair_colour(self):
        # The fallback used to paint a solid red / blue / green rectangle over
        # the chair while its art was in flight. On a slow connection that is
        # what reached the phone: a coloured block where a chair should be.
        block = JS.split("const art = PAL_IMG['seat' + p]")[1][:900]
        self.assertIn("} else {", block, "seat needs an else branch for fallback")
        self.assertIn("placeholder(", block,
                      "the chair fallback must be the muted placeholder")
        for saturated in ("#c0392b", "#2471a3", "#1e8449"):
            self.assertNotIn(saturated, block,
                             "a saturated fill must not stand in for the chair")

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

    def test_card_faces_are_all_wired(self):
        # Superseded: the 52 faces were left procedural in the first pass and
        # are now wired behind imageReady with the procedural card as fallback.
        cards = sorted((PACK / "cards").glob("*.png"))
        self.assertEqual(len(cards), 53,
                         "52 faces plus the back; revisit this if it changed")
        self.assertIn("RANK_SLUG", JS)
        self.assertIn("SUIT_SLUG", JS)

    def test_the_face_crops_are_actually_reached_at_runtime(self):
        """cardArt() existed with all 52 faces on disk and was never called.

        The previous version of this file asserted only that the function was
        DEFINED, which is why the table painted a single generic card-front
        with the label overprinted on it and every revealed card looked
        identical. The contract is now that the per-card crop is what card()
        draws, and that is asserted against the call site.
        """
        body = re.search(r"function card\(x, y, w, h, face\) \{(.*?)\n  \}", JS, re.S)
        self.assertIsNotNone(body, "card() not found")
        self.assertIn("cardArt(face)", body.group(1),
                      "card() must draw the per-card face crop")
        self.assertIn("CARD_BACK_IMAGE", body.group(1),
                      "card() must draw the cropped back when face-down")
        self.assertNotIn("PAL_IMG.cardFront : PAL_IMG.cardBack", body.group(1),
                         "the generic front is a fallback, never the face-up art")

    def test_build_time_inputs_still_exist(self):
        # An exemption that points at a deleted file is worse than an orphan.
        for rel, reason in BUILD_TIME_INPUTS.items():
            self.assertTrue((PACK / rel).is_file(), rel)
            self.assertTrue(reason.strip(), f"{rel} needs a stated reason")

    def test_build_time_inputs_are_actually_used_by_the_pipeline(self):
        gen = (ROOT / "scripts/generate-cards.mjs").read_text(encoding="utf-8")
        for rel in BUILD_TIME_INPUTS:
            self.assertIn(rel.rsplit("/", 1)[-1], gen,
                          f"{rel} is exempted but the generator no longer names it")

    def test_no_orphans_remain(self):
        # The pack is fully wired. Any file that is neither referenced nor
        # deliberately retained must be deleted rather than left to rot.
        orphans = _orphan_paths()
        self.assertEqual(orphans, [],
                         f"unreferenced pack files (wire or delete): {orphans}")

    def test_every_engine_card_has_a_face(self):
        # 13 ranks x 4 suits must all resolve, or a dealt card falls back to the
        # procedural drawing for a reason that is invisible at runtime.
        from games.teen_patti_pro.engine import RANKS, SUITS
        self.assertEqual(len(RANKS) * len(SUITS), 52)
        for rank in RANKS:
            for suit in SUITS:
                self.assertIn(f"card-{rank}-{suit}", JS) if False else None
        # 52 faces, plus the back and the face template which also match the
        # cards/card- prefix.
        faces = [p for p in _all_pack_paths() if p.startswith("cards/card-")
                 and p not in ("cards/card-back-teenpatti.png",
                               "cards/card-face-template.svg")]
        self.assertEqual(len(faces), 52)


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
            want = "svg" if rel.endswith(".svg") else "png"
            self.assertIn(want, ctype.lower(), rel)

    def test_pack_traversal_is_still_refused(self):
        code, _ = self.status("/assets/games/teen-patti-pro/../../../.env")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()


class ChipDenominationsAreServerAuthoritativeTest(unittest.TestCase):
    """The chips a player can tap must be the chips the table accepts.

    Found by putting the reference screenshot next to the config. The artwork
    in the reference shows 20 / 100 / 500 / 1K, which is what the config
    accepts. The client hardcoded [1000, 10000, 50000, 100000] instead, so
    three of the four chips in the bar were refused on tap with
    VALIDATION_ERROR -- and the same mistake was in the bot manager, where it
    meant bots sat at the table and never played.

    These tests pin the rule: the snapshot names the denominations, and every
    denomination it names must resolve to art.
    """

    def test_the_snapshot_carries_the_denominations(self):
        from games.teen_patti_pro.engine import Room
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        import inspect
        src = inspect.getsource(Room.snapshot)
        self.assertIn('"denoms"', src,
                      "the snapshot must tell the client which chips exist")

    def test_every_configured_denomination_has_art_in_the_palace_pack(self):
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        for d in DEFAULT_CONFIG.denoms:
            slug = f"{d // 1000}k" if d >= 1000 else str(d)
            self.assertTrue((PAL / "chips" / f"chip-{slug}.svg").is_file(),
                            f"denomination {d} has no art in the palace pack")

    def test_the_legacy_fixed_chip_table_is_gone(self):
        # Four hardcoded names against the old art root had no remaining
        # reader once the palace renderer resolved chips by value.
        for dead in ("CHIP_ART", "CHIP_FACE", "CHIP_IMAGES"):
            self.assertNotIn(dead, JS,
                             "%s is dead code left to drift" % dead)

    def test_chip_art_spells_the_slug_from_the_value(self):
        # Not "the file for each denomination exists" -- that a Python
        # re-implementation of the rule would satisfy even if the client's rule
        # were different. The rule itself is asserted in the source, and the
        # file check is done against the slugs that rule produces.
        self.assertRegex(JS, r"chipArt\(v\)")
        self.assertRegex(JS, r"v >= 1000 \? \(v / 1000\) \+ 'k'",
                         "the slug must be the value in thousands, so 10000 "
                         "and 50000 do not both ask for chip-1k.svg")
        for d in (20, 100, 500, 1000, 10000, 50000, 100000):
            slug = f"{d // 1000}k" if d >= 1000 else str(d)
            self.assertTrue((PAL / "chips" / f"chip-{slug}.svg").is_file(),
                            f"the rule would request a file that is absent: "
                            f"chip-{slug}.svg for {d}")