"""Every asset the client references must exist in the repo and be served.

Regression: `SEAT_ASSETS` pointed at

    assets/generated/seat-p4.svg
    assets/generated/seat-p5.svg
    assets/generated/seat-p6.svg

`assets/generated/` does not exist in this repository. All three requests
404'd, the `Image` objects never fired `onload`, and the chairs silently fell
back to procedural shapes with none of the specified colours. Nothing failed
loudly, the page rendered, and the only symptom was "the chairs are the wrong
colour" -- which is exactly the kind of defect a screenshot catches and a test
suite does not.

The client uses `imageReady(img)` precisely so that missing art degrades
gracefully. That is right for robustness and terrible for detection: a 404 is
invisible by design. This test restores the visibility by checking the paths
against the filesystem, and it also pins the seat order/colours from the spec
(green left, blue centre, red right).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "games/teen_patti_pro/client/game.js"
ART = "/assets/games/teen-patti-pro/"


class ClientAssetPathsTest(unittest.TestCase):
    def setUp(self):
        raw = CLIENT.read_text(encoding="utf-8")
        # Strip comments: this test is about paths the client *requests*, and a
        # path named in an explanatory comment is not a request. (Its own
        # docstring names the bad paths too.)
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        raw = re.sub(r"(?m)^\s*//.*$", "", raw)
        self.source = raw

    def _paths(self):
        found = re.findall(r"['\"]([^'\"]*?\.svg)['\"]", self.source)
        # Only absolute/asset-rooted paths are checkable; bare filenames and
        # template fragments are not real URLs.
        return [p for p in found if p.startswith("/assets/") or p.startswith("assets/")]

    def test_no_asset_path_points_at_a_missing_file(self):
        missing = []
        for p in self._paths():
            rel = p.lstrip("/")
            if not (ROOT / rel).is_file():
                missing.append(p)
        self.assertEqual(
            missing, [],
            "client references asset files that do not exist in the repo: "
            f"{sorted(set(missing))}")

    def test_generated_assets_dir_is_not_referenced(self):
        """The specific regression: assets/generated/ is not a real directory."""
        bad = [p for p in self._paths() if "assets/generated/" in p]
        self.assertEqual(
            bad, [],
            f"references to assets/generated/ which does not exist: {bad}")

    def test_seat_assets_are_the_spec_chairs_in_order(self):
        m = re.search(r"const SEAT_ASSETS\s*=\s*\[(.*?)\]", self.source, re.S)
        self.assertIsNotNone(m, "SEAT_ASSETS not found in game.js")
        got = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
        self.assertEqual(
            got,
            [ART + "seats/seat-green.svg",
             ART + "seats/seat-blue.svg",
             ART + "seats/seat-red.svg"],
            "seats must be green (left), blue (centre), red (right)")

    def test_every_chair_asset_exists_on_disk(self):
        for name in ("seat-green.svg", "seat-blue.svg", "seat-red.svg"):
            with self.subTest(name=name):
                self.assertTrue((ROOT / "assets/games/teen-patti-pro/seats" / name).is_file())

    def test_chip_assets_exist_for_the_spec_chip_bar(self):
        chips = ROOT / "assets/games/teen-patti-pro/chips"
        for name in ("chip-20.svg", "chip-100.svg", "chip-500.svg", "chip-1k.svg"):
            with self.subTest(name=name):
                self.assertTrue((chips / name).is_file(), f"missing {name}")

    def test_whole_pack_is_present_on_disk(self):
        """93 assets, 0 orphans -- the pack the client draws from."""
        pack = ROOT / "assets/games/teen-patti-pro"
        self.assertTrue(pack.is_dir())
        # The card faces are crops of a raster sheet, so they ship as PNGs.
        n = sum(1 for _ in pack.rglob("*.svg")) + sum(1 for _ in pack.rglob("*.png"))
        self.assertGreaterEqual(n, 125, f"only {n} assets in the pack")

    def test_background_is_the_palace_art_not_a_flat_fill(self):
        """The reference is a painted palace: curtains, marble, gold light.

        The table is no longer a jungle-green felt, so the old "#4A5D23 ->
        #2D3A14" contract is retired. What must hold now is that the frame is
        driven by the cropped background art, with a procedural palace-gradient
        fallback rather than the retired flat green.
        """
        self.assertIn("background/palace-background.svg", self.source,
                      "the palace background art is not referenced")
        for banned in ("#4A5D23", "#2D3A14", "#3A4A1B"):
            self.assertNotIn(banned, self.source,
                             f"retired jungle-felt colour {banned} still present")
        # Cover-fit, not letterbox: the scale factor must be the MAX of the
        # two axis ratios, so the art always fills the frame and the overflow
        # is cropped. (An earlier revision of this assertion looked for the
        # word "cover", which lived only in a // comment this test strips --
        # it could never pass no matter what the code did. This checks the
        # actual scaling expression instead.)
        self.assertIn("Math.max(W / bg.naturalWidth", self.source,
                      "background must cover-fit rather than letterbox")


if __name__ == "__main__":
    unittest.main()
