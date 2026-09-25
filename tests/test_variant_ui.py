"""UI regression: the confirmed 3-seat variant must not show traditional
Teen Patti controls, and the landing page must only link the 3 live games.

Naming decision of record: Teen Patti Pro is a 3-seat highest-hand /
seat-betting variant. Blind, Chaal, Pack/Fold-as-Teen-Patti, Show, Side Show
and multi-round betting are out of scope and must never be rendered, however
traditional they look. See games/teen_patti_pro/plugin.py.

The action buttons were drawn on a <canvas> rather than in HTML, so an HTML
scan alone would not have found them.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "games", "teen_patti_pro", "client")

FORBIDDEN = ("blind", "chaal", "sideshow", "side show", "side_show")
# "pack" and "show" are only forbidden as standalone action labels; they occur
# legitimately in words like "package" and "shown", so match them as labels.
FORBIDDEN_LABELS = ("'pack'", '"pack"', "'show'", '"show"')


def _read(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def _read_code(path):
    """Source with comments stripped.

    Comments are documentation, not rendered UI. game.js explains WHY the
    traditional buttons are absent and has to name them to do so; that must not
    trip the guard, while any label or copy that actually reaches the screen
    still does.
    """
    src = _read(path)
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", " ", src, flags=re.M)
    src = re.sub(r"//[^\n\"'`]*$", " ", src, flags=re.M)
    return src


class TraditionalControlsAbsentTest(unittest.TestCase):
    def test_client_js_has_no_traditional_action_labels(self):
        src = _read_code(os.path.join(CLIENT, "game.js")).lower()
        for term in FORBIDDEN:
            self.assertNotIn(term, src,
                             f"{term!r} must not appear in the game client")
        for term in FORBIDDEN_LABELS:
            self.assertNotIn(term, src,
                             f"action label {term} must not appear")

    def test_no_action_button_array_is_defined(self):
        src = _read_code(os.path.join(CLIENT, "game.js"))
        self.assertNotRegex(src, r"actionLabels\s*=",
                            "the BLIND/CHAAL/PACK/SHOW/SIDESHOW strip is gone")
        self.assertNotIn("_actions.push", src,
                         "no hit-testable traditional action buttons")

    def test_no_html_client_declares_them(self):
        for name in os.listdir(CLIENT):
            if not name.endswith((".html", ".js")):
                continue
            src = _read_code(os.path.join(CLIENT, name)).lower()
            for term in FORBIDDEN:
                self.assertNotIn(term, src, f"{name}: {term!r}")

    def test_confirmed_variant_controls_are_present(self):
        """The controls that DO apply must survive the removal."""
        src = _read(os.path.join(CLIENT, "game.js"))
        for needle, why in (("_chips", "chip denomination bar"),
                            ("_repeat", "Repeat bet button"),
                            ("placeBet", "bet placement"),
                            ("selPos", "seat selection")):
            self.assertIn(needle, src, f"{why} must remain")


class LandingSlugTest(unittest.TestCase):
    """The landing page must link only the 3 live games.

    `monkey-wheel` is deliberately NOT a wrong slug for baby-king: it is a
    legacy ALIAS of greedy-monkey and the backend canonicalises it
    (provider/games.py, staging/wsgi.py). Rewriting it here would repoint old
    links at the wrong game.
    """

    LIVE = ("teen-patti-pro", "greedy-monkey", "baby-king")

    def test_landing_links_exactly_the_three_live_games(self):
        src = _read(os.path.join(ROOT, "index.html"))
        linked = re.findall(r'data-game="([^"]+)"', src)
        self.assertEqual(sorted(linked), sorted(self.LIVE),
                         "landing page must link exactly the 3 live games")

    def test_landing_has_no_archived_lion(self):
        src = _read(os.path.join(ROOT, "index.html")).lower()
        self.assertNotIn("greedy lion", src)
        self.assertNotIn("greedy-lion", src,
                         "greedy-lion is archived per the 3-game scope lock")

    def test_landing_does_not_mislabel_monkey_wheel(self):
        src = _read(os.path.join(ROOT, "index.html"))
        self.assertNotIn('data-game="monkey-wheel"', src,
                         "monkey-wheel is a legacy alias, not a catalog entry")


if __name__ == "__main__":
    unittest.main()
