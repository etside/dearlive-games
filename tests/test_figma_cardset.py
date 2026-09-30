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
