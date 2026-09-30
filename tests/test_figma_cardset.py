"""The Figma playing-card set: 3 designs x 52 faces + 2 backs.

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
import itertools
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "assets", "games", "teen-patti-pro", "cardsets", "figma")

RANKS = ["A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2"]
SUITS = ["heart", "spade", "diamond", "club"]
DESIGNS = ["d1", "d2", "d3"]


class FigmaCardSetTest(unittest.TestCase):
    def test_the_set_is_present(self):
        self.assertTrue(os.path.isdir(PACK), "figma cardset missing: %s" % PACK)

    def test_each_design_has_all_fifty_two_faces(self):
        want = {"card-%s-%s.png" % (r, s) for s, r in itertools.product(SUITS, RANKS)}
        for d in DESIGNS:
            have = {f for f in os.listdir(os.path.join(PACK, d)) if f.endswith(".png")}
            self.assertEqual(have, want,
                             "design %s is incomplete: missing %s"
                             % (d, sorted(want - have)[:6]))

    def test_both_backs_are_present(self):
        backs = {f for f in os.listdir(os.path.join(PACK, "backs"))
                 if f.endswith(".png")}
        self.assertEqual(backs, {"back-plain.png", "back-monogram.png"})

    def test_the_column_order_is_recorded_as_descending(self):
        """The whole point of PROVENANCE.md.

        If someone re-extracts and assumes ascending order, this is the only
        thing that flags it.
        """
        with open(os.path.join(PACK, "PROVENANCE.md"), encoding="utf-8") as fh:
            doc = fh.read()
        self.assertRegex(doc, r"A,\s*K,\s*Q,\s*J,\s*10",
                         "PROVENANCE must state the sheets run A,K,Q,J,10..2")
        self.assertIn("descending", doc.lower())

    def test_the_resolution_ceiling_is_recorded(self):
        with open(os.path.join(PACK, "PROVENANCE.md"), encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("59x87", doc,
                      "PROVENANCE must record the real per-card size")

    def test_faces_are_the_native_crop_not_an_upscale(self):
        """59x87 is the ceiling; a 122x178 crop would be a 2x interpolation."""
        from PIL import Image
        for d in DESIGNS:
            for name in ("card-A-heart.png", "card-K-spade.png", "card-2-club.png"):
                p = os.path.join(PACK, d, name)
                if not os.path.isfile(p):
                    continue
                w, h = Image.open(p).size
                self.assertLessEqual(w, 60, "%s/%s is %d wide -- upscaled?" % (d, name, w))
                self.assertLessEqual(h, 88, "%s/%s is %d tall -- upscaled?" % (d, name, h))


class FigmaSetIsNotSilentlyLiveTest(unittest.TestCase):
    """The staged set must not quietly become the default.

    The live art is 111x185; these are 59x87. If someone points the renderer
    at this directory without saying so, the table gets visibly softer cards
    and nothing fails. This asserts the renderer still points at the other set.
    """
    def test_the_renderer_does_not_reference_the_staged_set(self):
        with open(os.path.join(ROOT, "games", "teen_patti_pro", "client", "game.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        self.assertNotIn("cardsets/", js,
                         "the staged Figma set must not be wired in without a "
                         "deliberate decision: it is 59x87 against 111x185")


if __name__ == "__main__":
    unittest.main()


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
