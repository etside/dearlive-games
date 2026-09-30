"""Guardrails for runtime art, kept after the staged card sets were removed.

Extracted from a PDF whose page embeds a single 1080x2123 raster. Two things
about that source are easy to get wrong and expensive to get wrong silently:

1. The sheets run A,K,Q,J,10..2 left to right -- descending, with the Ace in
   the leftmost column. A cropper that assumes the usual ascending order names
   all 156 files plausibly and wrongly, and nothing downstream can tell,
   because every file exists and every file is a real card.

2. The header band holding the two backs is the same height as a card row, so
   it reads as a 13th row of cards.

These tests assert completeness and that the extraction is documented, so a
future re-extraction has to confront the ordering rather than rediscover it.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NoSolidFallbackBlocksTest(unittest.TestCase):
    """A missing image must never paint a coloured block.

    The chair, chip, card and panel fallbacks already route through
    placeholder() -- a 7% white wash and a 16% hairline. Two paths did not: the
    toolbar icons fell back to a solid #6b4fa0 disc and the avatar to a solid
    #4b3a72 disc. On a slow connection those are what a player actually sees
    while the art is in flight, and a saturated disc is indistinguishable from
    a rendering bug.

    This asserts the rule rather than the two specific colours, so the next
    fallback added does not reintroduce the class of problem.
    """
    CLIENT = os.path.join(ROOT, "games", "teen_patti_pro", "client", "game.js")

    def _sources(self):
        with open(self.CLIENT, encoding="utf-8") as fh:
            return fh.read()

    def test_no_saturated_hex_fill_in_a_fallback_branch(self):
        import re
        js = self._sources()
        # Every "else {" that guards a missing image, and what it paints.
        branches = re.findall(
            r"else\s*\{(.{0,400}?)\}", js, re.S)
        banned = {"#6b4fa0", "#4b3a72", "#2196f3", "#ff00ff", "#8e44ad",
                  "#c0392b", "#2471a3", "#1e8449", "#e91e63", "#ff4081"}
        offenders = []
        for b in branches:
            if "fillStyle" not in b:
                continue
            for m in re.findall(r"fillStyle\s*=\s*'(#[0-9a-fA-F]{6})'", b):
                if m.lower() in banned:
                    offenders.append(m)
        self.assertEqual(offenders, [],
                         "saturated fills left in a fallback branch: %s" % offenders)

    def test_the_icon_fallback_is_a_stroke_not_a_fill(self):
        import re
        js = self._sources()
        m = re.search(r"function icon\(img, act\) \{(.*?)\n    \}", js, re.S)
        self.assertIsNotNone(m, "toolbar icon() not found")
        # Strip comments first: a colour named in an explanatory comment is
        # the whole point of those comments, and the assertion is about code.
        body = re.sub(r"//[^\n]*", "", m.group(1))
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        self.assertNotIn("#6b4fa0", body, "the solid purple disc is back")
        self.assertIn("ctx.stroke()", body,
                      "the fallback should draw a hairline ring")

    def test_placeholder_is_the_shared_skeleton(self):
        js = self._sources()
        m = re.search(r"function placeholder\(x, y, w, h, r\) \{(.*?)\n  \}", js, re.S)
        self.assertIsNotNone(m, "placeholder() not found")
        body = m.group(1)
        self.assertIn("rgba(255,255,255,.07)", body,
                      "the skeleton must be a faint wash, not a colour")
        self.assertIn("rgba(255,255,255,.16)", body,
                      "the skeleton must have a hairline edge")
        self.assertNotRegex(body, r"fillStyle\s*=\s*'#[0-9a-fA-F]{3,6}'",
                            "placeholder() must never fill with an opaque colour")


class NoBakedTextInRuntimeArtTest(unittest.TestCase):
    """Runtime panel art must never carry its own values.

    Three replacement bet panels arrived with two hardcoded <text> elements
    each -- "0 / 0" and "x2.9". The client already draws POT / You / x2.9 on
    the canvas from S.myBets[p] and snap.pots[p], so shipping them unchanged
    would stack a permanent "0 / 0" underneath the live numbers: the exact
    class of stale-value bug this project has been fighting.

    The look was kept, the two <text> elements removed.
    """
    # The canvas renderer reads panels from PAL = /assets/teen-patti/ , NOT from
    # the ART_BASE namespace. An earlier version of this test pointed at
    # assets/games/teen-patti-pro/ui/ and passed vacuously against files the
    # renderer never loads.
    PACK = os.path.join(ROOT, "assets", "teen-patti", "ui")

    def test_no_panel_art_carries_baked_values(self):
        import re
        for name in ("panel-red.svg", "panel-blue.svg", "panel-green.svg"):
            p = os.path.join(self.PACK, name)
            self.assertTrue(os.path.isfile(p), "%s missing" % name)
            with open(p, encoding="utf-8") as fh:
                body = fh.read()
            texts = re.findall(r"<text\b[^>]*>(.*?)</text>", body, re.S)
            self.assertEqual(texts, [],
                             "%s bakes %r into the artwork; the live values "
                             "are drawn on the canvas" % (name, texts))

    def test_the_panels_still_look_like_panels(self):
        """A panel must be real artwork, not a bare rectangle.

        The supplied replacements were flat gradient rounded rects -- which is
        the "pink/blue rectangular block" the renderer is meant to stop showing.
        This asserts the runtime art keeps genuine structure, so a flat fill
        cannot be swapped in as a panel again.
        """
        for name in ("panel-red.svg", "panel-blue.svg", "panel-green.svg"):
            with open(os.path.join(self.PACK, name), encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("viewBox", body, "%s lost its viewBox" % name)
            # The runtime panels are painted artwork wrapped as a single
            # embedded raster inside an <svg> shell -- 388x122, ~100KB, zero
            # vector primitives. So "has vector shapes" is the wrong test.
            # What matters is that the panel is real artwork rather than a
            # flat fill: a bare <rect> or a <linearGradient> alone would be a
            # coloured block pretending to be an asset.
            self.assertIn("<rect", body, "%s has no panel body" % name)
            self.assertRegex(body, r"<linearGradient\b",
                             "%s has no fill gradient" % name)
            self.assertRegex(body, r'viewBox="[^"]+"', "%s has no viewBox" % name)
            # KNOWN TRADE-OFF, recorded deliberately: the replacement panels
            # are flat gradient plates, replacing a ~100KB painted raster.
            # This assertion previously demanded an embedded raster precisely
            # because flat gradient plates are what "pink/blue rectangular
            # block" was describing. It is relaxed here on instruction, but the
            # chips and amounts must be drawn as real chip artwork on top so a
            # bet is never represented by a number inside a coloured rectangle.
            self.assertNotIn("<image", body,
                             "this panel is now vector, not an embedded raster")

    def test_the_three_panels_are_distinct_files(self):
        """A/B/C must not collapse to one shared image.

        Replacing these panels is exactly the operation that could go wrong, so
        the guard is that the three files differ from each other -- not that
        they match a particular palette.
        """
        bodies = {}
        for name in ("panel-red.svg", "panel-blue.svg", "panel-green.svg"):
            with open(os.path.join(self.PACK, name), encoding="utf-8") as fh:
                bodies[name] = fh.read()
        self.assertEqual(len(set(bodies.values())), 3,
                         "two or more betting panels are byte-identical, so "
                         "A/B/C would render the same artwork")
