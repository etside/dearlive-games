"""The client's draw loop must not throw.

Why this exists
---------------
The table rendered the palace background and nothing else. The cause was not
the seat-field mismatch people assumed -- it was that `num()`, `rr()` and
`card()` were called from the draw path and defined nowhere in the file. The
first call site was inside `palaceToolbar`, the second function in the chain,
so the first frame drew the background and then threw `ReferenceError`.

Because `requestAnimationFrame(draw)` is the *last* statement in `draw()`, the
exception killed the loop permanently: one frame with a background, then
nothing, forever. Nothing reached the server log, and the symptom -- an empty
table -- looks identical to a rendering or data problem. Four separate people
reasonably assumed it was the `seats` field.

A static check cannot find this class of bug: the calls all look fine in
isolation, and a grep for the definition is only as good as the reader's
pattern. So these tests *execute* the real `game.js` against a stub canvas and
require it to complete a frame. `tests/render_harness.js` is that harness; it
is invoked as a subprocess and its output is asserted on.

Two further guards live here because they are the same failure seen from
different angles: every function the client calls must be defined somewhere in
the shipped files, and the draw loop must survive the states the server
actually sends.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "games" / "teen_patti_pro" / "client"
GAME_JS = CLIENT / "game.js"
HARNESS = ROOT / "tests" / "render_harness.js"
# Records every drawImage/fillRect box and prints any that fall outside the
# canvas, plus a dump of the computed layout. Both are generated from the real
# client rather than a model of it.
GEOM = ROOT / "tests" / "render_geometry.js"
LAYOUT_PROBE = ROOT / "tests" / "layout_probe.js"

# Identifiers that are provided by the host rather than by the client.
HOST_PROVIDED = {
    "window", "document", "navigator", "location", "console", "Math", "JSON",
    "Date", "Object", "Array", "String", "Number", "Boolean", "Promise", "Set",
    "Map", "RegExp", "Error", "Symbol", "isFinite", "parseInt", "parseFloat",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame", "fetch", "Image",
    "WebSocket", "URL", "Blob", "Uint8Array", "decodeURIComponent",
    "encodeURIComponent", "matchMedia", "if", "for", "while", "switch",
    "catch", "return", "typeof", "function", "new", "delete", "void", "in",
    "of", "do", "else", "try", "throw", "case",
}

# The two states the server really sends. Both are exercised because the empty
# one is what a player sees first, and it is the state in which every optional
# field is absent.
LIVE_SNAPSHOT = {
    "room_id": "teen-patti-low", "round_id": "r1", "round_no": 1,
    "status": "BETTING_OPEN", "serverTime": 1, "betting_end_at": 9999999999999,
    "pots": {"A": 100}, "pot_total": 100, "my_bet": 0, "carry_in": 0,
    "hands": {"A": ["**", "**", "**"], "B": ["**", "**", "**"],
              "C": ["**", "**", "**"]},
    "winners": [], "config_version": "v", "currency": "COIN",
    "seatOccupancy": {"A": "p1", "B": None, "C": None},
    "members": [{"playerId": "p1", "seat": "A", "joinedAt": 1,
                 "status": "seated"}],
    "mySeat": "A", "isSpectator": False, "availableSeats": ["B", "C"],
    "balance": 10000, "denoms": [20, 100, 500, 1000], "max_bet": 100000,
}
IDLE_SNAPSHOT = {
    "room_id": "teen-patti-low", "round_id": "", "round_no": 0, "status": "WAITING",
    "serverTime": 1, "betting_end_at": None, "pots": {}, "pot_total": 0,
    "my_bet": 0, "carry_in": 0, "hands": {}, "winners": [],
    "config_version": "v", "currency": "COIN",
    "seatOccupancy": {"A": None, "B": None, "C": None}, "members": [],
    "mySeat": None, "isSpectator": True, "availableSeats": ["A", "B", "C"],
    "balance": 10000, "denoms": [], "max_bet": 100000,
}


def _run_harness(snapshot):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(snapshot, fh)
        path = fh.name
    try:
        return subprocess.run(["node", str(HARNESS), path],
                              capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)


class DrawLoopExecutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node is required to execute the client")

    def test_a_live_snapshot_renders_a_full_frame(self):
        r = _run_harness(LIVE_SNAPSHOT)
        self.assertEqual(r.returncode, 0,
                         "the draw loop threw on a live snapshot:\n"
                         + r.stdout + r.stderr)
        self.assertIn("render completed, no exception", r.stdout)
        for call in ("drawImage", "fillText", "arc", "fillRect"):
            self.assertIn(call, r.stdout,
                          "the frame never reached %s" % call)

    def test_an_idle_snapshot_renders_a_full_frame(self):
        # The state a player sees before the socket delivers anything: no
        # round, no seats, no denominations. Every optional field is absent,
        # so this is where a null reaching a canvas call shows up.
        r = _run_harness(IDLE_SNAPSHOT)
        self.assertEqual(r.returncode, 0,
                         "the draw loop threw on an idle snapshot:\n"
                         + r.stdout + r.stderr)
        self.assertIn("render completed, no exception", r.stdout)

    def test_the_artwork_path_is_exercised_not_only_the_fallback(self):
        # With a permanently-broken Image every imageReady() is false and the
        # harness silently tests only the fallback rendering, which is not the
        # path that was broken.
        r = _run_harness(LIVE_SNAPSHOT)
        self.assertIn("drawImage", r.stdout,
                      "no artwork was drawn; the harness is not testing the "
                      "real rendering path")


class UndefinedCalleeTest(unittest.TestCase):
    """Every function the client calls must be defined in a shipped file.

    This is the static counterpart to the execution test, and it exists
    because the execution test needs node. It found num/rr/card; a fourth such
    bug would be caught by CI even where node is unavailable.
    """

    def _shipped_sources(self):
        return [GAME_JS.read_text(encoding="utf-8"),
                (CLIENT / "index.html").read_text(encoding="utf-8")]

    def test_no_called_function_is_undefined(self):
        src = GAME_JS.read_text(encoding="utf-8")
        # Strip comments and string literals: a word in prose, or in a CSS
        # transform like "rotate(30deg)", is not a call.
        body = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        body = re.sub(r"//[^\n]*", "", body)
        body = re.sub(r"'(?:\\.|[^'\\])*'", "''", body)
        body = re.sub(r'"(?:\\.|[^"\\])*"', '""', body)
        body = re.sub(r"`(?:\\.|[^`\\])*`", "``", body)

        defined = set()
        for text in self._shipped_sources():
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            text = re.sub(r"//[^\n]*", "", text)
            defined |= set(re.findall(
                r"\b(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", text))
            # Parameters and destructured names are bindings too. Without
            # this, every callback -- onFrame, onComplete, resolve, fn -- reads
            # as an undefined global and the check is pure noise.
            for params in re.findall(r"function[^()]*\(([^)]*)\)", text):
                for pname in re.split(r",", params):
                    pname = pname.strip().split("=")[0].strip()
                    if re.fullmatch(r"[A-Za-z_$][\w$]*", pname or ""):
                        defined.add(pname)
            for arrow in re.findall(r"\(([^)]*)\)\s*=>", text):
                for pname in re.split(r",", arrow):
                    pname = pname.strip().split("=")[0].strip()
                    if re.fullmatch(r"[A-Za-z_$][\w$]*", pname or ""):
                        defined.add(pname)
            # A destructured parameter binds its keys:
            #   function animate({ duration, onFrame, onComplete })
            for params in re.findall(r"\(([^)]*)\)", text):
                for part in re.findall(r"\{([^}]*)\}", params):
                    for key in re.split(r",", part):
                        key = key.strip().split(":")[-1].split("=")[0].strip()
                        if re.fullmatch(r"[A-Za-z_$][\w$]*", key or ""):
                            defined.add(key)
            defined |= set(re.findall(
                r"\b(?:const|let|var)\s+\{?([^}=;]+?)\s*\}?\s*=", text)
                and [n.strip().split(":")[-1].strip()
                     for n in re.findall(
                         r"\b(?:const|let|var)\s+\{([^}]*)\}\s*=", text)])
        called = set(re.findall(r"(?<![.\w$])([a-z_$][\w$]*)\s*\(", body))
        missing = sorted(c for c in called
                         if c not in defined and c not in HOST_PROVIDED)
        self.assertEqual(missing, [],
                         "the client calls functions it never defines: %s. "
                         "The first one hit in the draw chain stops requestAnimationFrame, "
                         "so the table renders one frame and then nothing." % missing)

    def test_the_helpers_the_draw_chain_needs_exist(self):
        src = GAME_JS.read_text(encoding="utf-8")
        for fn in ("num", "rr", "card", "potPos", "palaceLayout", "layout"):
            self.assertRegex(
                src, r"function %s\(" % re.escape(fn),
                "%s() is called by the renderer and is not defined" % fn)

    def test_the_draw_loop_reschedules_itself_last(self):
        # The reason one throw produced a permanently blank table. If a future
        # edit adds a call after the reschedule, an exception would no longer
        # stop the loop and this failure mode would be masked.
        src = GAME_JS.read_text(encoding="utf-8")
        i = src.index("function draw(now) {")
        j = src.index("\n  }\n", i)
        body = src[i:j]
        raf = body.find("requestAnimationFrame(draw)")
        self.assertNotEqual(raf, -1, "draw() must reschedule itself")
        self.assertEqual(raf, body.rfind("requestAnimationFrame(draw)"),
                         "requestAnimationFrame(draw) must be the last thing "
                         "draw() does, or a throw no longer stops the loop")
        # Nothing may follow the reschedule. The body slice stops at the
        # function's closing brace, so "nothing after it" is the assertion.
        trailing = body[raf + len("requestAnimationFrame(draw);"):].strip()
        self.assertIn(trailing, ("", "}"),
                      "only the closing brace may follow the reschedule, "
                      "found: %r" % trailing)


if __name__ == "__main__":
    unittest.main()


class OnScreenGeometryTest(unittest.TestCase):
    """Every drawn box must land inside the canvas.

    Found by measuring, not by looking. palaceLayout assigned y.bottom inside
    the band loop and then overwrote it with the running total, which put the
    chip-bar band at exactly H on an H-tall canvas. The balance pill, the chip
    row and the Repeat button were all drawn below the visible area, so three
    separate "missing element" reports had one cause.

    This executes the client, records every drawImage/fillRect, and asserts
    the boxes are in bounds -- so an element that is drawn off-screen fails
    here instead of being reported three times as absent.
    """
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node is required")

    def _boxes(self, snapshot):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(snapshot, fh)
            path = fh.name
        try:
            r = subprocess.run(["node", str(GEOM), path],
                               capture_output=True, text=True, timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_no_element_is_drawn_off_screen(self):
        out = self._boxes(LIVE_SNAPSHOT)
        self.assertIn("OFF-SCREEN: 0", out,
                      "an element is drawn outside the canvas:\n" + out)

    def test_the_chip_bar_band_is_inside_the_viewport(self):
        # The specific regression: the band must start above the canvas
        # height, or the bar, the balance pill and Repeat all vanish.
        probe = subprocess.run(["node", str(LAYOUT_PROBE)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        m = re.search(r"bottom bar: y=([\d.]+) \.\. ([\d.]+)\s+\(H=(\d+)\)",
                      probe.stdout)
        self.assertIsNotNone(m, probe.stdout)
        start, end, height = float(m.group(1)), float(m.group(2)), float(m.group(3))
        self.assertLess(start, height,
                        "the chip-bar band starts at or below the canvas "
                        "bottom, so nothing in it is visible")
        self.assertLessEqual(end, height + 1,
                             "the chip-bar band runs past the canvas bottom")

    def test_cards_are_three_separated_groups_of_three(self):
        out = self._boxes(LIVE_SNAPSHOT)
        # Derive the card row from the boxes instead of pinning y and width.
        # The hand is sized and placed from the layout grid, so a hardcoded
        # "y=196, w=33" only ever described one canvas size and failed the
        # moment the card geometry was legitimately retuned.
        boxes = [(int(x), int(y), int(w), int(h)) for x, y, w, h in
                 re.findall(r"x=\s*(-?\d+)\s+y=\s*(-?\d+)\s+w=\s*(\d+)\s+h=\s*(\d+)", out)]
        cards = [b for b in boxes if 15 <= b[0] and b[0] + b[2] <= 390
                 and 30 <= b[1] <= 420 and b[2] < b[3] and b[2] <= 60]
        self.assertEqual(len(cards), 9,
                         "expected nine cards on the card row, got %d: %s"
                         % (len(cards), cards))
        card_w = cards[0][2]
        starts = [c[0] for c in sorted(cards)]
        self.assertEqual(len({c[1] for c in cards}), 1,
                         "all nine cards must share one row")
        gaps = [b - (a + card_w) for a, b in zip(starts, starts[1:])]
        within = [g for g in gaps if g < 15]
        between = [g for g in gaps if g >= 15]
        self.assertEqual(len(within), 6, "six within-group gaps expected")
        self.assertEqual(len(between), 2, "two between-group gaps expected")
        # Nine cards with a smaller gap between groups than within one read as
        # a single row across the table.
        self.assertTrue(between and min(between) > max(within) * 2,
                        "the gap between card groups (%s) must be clearly "
                        "larger than the gap within one (%s)" % (between, within))
