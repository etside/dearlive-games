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
