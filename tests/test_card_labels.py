"""Card faces and the timer badge must speak the player's language.

Two defects lived here. Card faces were painted with the engine's wire code,
so a king of diamonds rendered as the literal string "13D" -- a player cannot
read that as a card. And the countdown badge fell back to printing the raw
RoundStatus enum, so "CLOSED" appeared where a number belonged.

The mapping is executed, not grepped: the source of the shipped functions is
extracted from game.js and run, so a typo in the table fails here.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "games", "teen_patti_pro", "client", "game.js")
ENGINE = os.path.join(ROOT, "games", "teen_patti_pro", "engine.py")
LIFECYCLE = os.path.join(ROOT, "common", "lifecycle.py")


def _grab(pattern, src):
    m = re.search(pattern, src, re.S)
    if not m:
        raise AssertionError("could not extract %r from game.js" % pattern)
    return m.group(0)


class CardLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CLIENT, encoding="utf-8") as fh:
            src = fh.read()
        cls.js = src
        code = "\n".join([
            _grab(r"const RANK_LABELS = \{.*?\};", src),
            _grab(r"const SUIT_LABELS = \{.*?\};", src),
            _grab(r"function formatCard\(raw\) \{.*?\n  \}", src),
            _grab(r"function timerLabel\(status, secs\) \{.*?\n  \}", src),
        ])
        cls.code = code
        cls.cache = {}

    def _run(self, probes):
        """Execute the shipped functions on a list of probes.

        The functions live inside the client's IIFE, so the source is lifted
        out and run on its own. That keeps this a behavioural test of the
        code that ships rather than a regex over it.
        """
        key = json.dumps(probes, sort_keys=True)
        if key in self.cache:
            return self.cache[key]
        driver = self.code + """
const probes = %s;
const out = probes.map(function (p) {
  try {
    return p[0] === 'card' ? formatCard(p[1])
                           : timerLabel(p[1], p.length > 2 ? p[2] : null);
  } catch (e) { return 'THREW:' + e.message; }
});
process.stdout.write(JSON.stringify(out));
""" % json.dumps(probes)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(driver)
            path = fh.name
        out = subprocess.run([_node(), path], capture_output=True, text=True)
        if out.returncode != 0:
            self.fail("extracted card/timer code did not run: %s" % out.stderr)
        res = json.loads(out.stdout)
        self.cache[key] = res
        return res

    def fmt(self, raw):
        return self._run([["card", raw]])[0]

    def label(self, status, secs=None):
        probe = ["timer", status] if secs is None else ["timer", status, secs]
        return self._run([probe])[0]

    # ---- card faces ---------------------------------------------------
    def test_face_cards_read_as_cards_not_as_wire_codes(self):
        self.assertEqual(self.fmt("13D"), "K\u2666")
        self.assertEqual(self.fmt("11C"), "J\u2663")
        self.assertEqual(self.fmt("12H"), "Q\u2665")
        self.assertEqual(self.fmt("14S"), "A\u2660")

    def test_number_cards_are_unchanged(self):
        for raw, want in (("2S", "2\u2660"), ("7D", "7\u2666"),
                          ("9C", "9\u2663"), ("10H", "10\u2665")):
            self.assertEqual(self.fmt(raw), want)

    def test_alternative_encodings_parse_to_the_same_face(self):
        # "13-D" and an object form must not reach the canvas as raw text.
        self.assertEqual(self.fmt("13-D"), "K\u2666")
        self.assertEqual(self.fmt({"rank": 13, "suit": "D"}), "K\u2666")

    def test_face_down_and_unknown_values_pass_through(self):
        self.assertEqual(self.fmt("**"), "**")
        self.assertEqual(self.fmt(""), "")
        self.assertEqual(self.fmt(None), "")

    def test_the_joker_sentinel_is_named(self):
        # The engine's joker is (0, "J"); drawn raw it reads as "0J".
        self.assertEqual(self.fmt("0J"), "JOKER")

    def test_the_table_covers_every_rank_the_engine_can_deal(self):
        """Guards against drift in either direction.

        The client table is hand-written and the engine's rank range is a
        constant. If either moves, a dealt card silently renders as its
        number again.
        """
        with open(ENGINE, encoding="utf-8") as fh:
            eng = fh.read()
        m = re.search(r"^RANKS = tuple\(range\((\d+),\s*(\d+)\)\)", eng, re.M)
        self.assertIsNotNone(m, "engine RANKS range not found")
        # range() is exclusive of its upper bound: range(2, 15) is 2..14.
        lo, hi = int(m.group(1)), int(m.group(2)) - 1
        table = dict(re.findall(r"(\d+):\s*'([^']+)'", _grab(r"const RANK_LABELS = \{.*?\};", self.js)))
        for r in range(lo, hi + 1):
            self.assertIn(str(r), table, "rank %d has no label" % r)
        self.assertEqual(table["11"], "J")
        self.assertEqual(table["12"], "Q")
        self.assertEqual(table["13"], "K")
        self.assertEqual(table[str(hi)], "A", "the top rank must be an ace")

    def test_card_rendering_goes_through_the_mapper(self):
        self.assertRegex(self.js, r"fillText\(formatCard\(face\)",
                         "the card face must be painted with the mapped label")

    # ---- timer badge --------------------------------------------------
    def test_betting_shows_the_remaining_seconds(self):
        self.assertEqual(self.label("BETTING_OPEN", 12.2), "13")
        self.assertEqual(self.label("BETTING_OPEN", 7), "7")

    def test_lifecycle_states_map_to_readable_glyphs(self):
        cases = {
            "BETTING_CLOSED": "0",
            "RESULT_PROCESSING": "\u2026",
            "RESULT": "\u2605",
            "SETTLED": "\u2605",
            "UPCOMING": "0",
            "CLOSED": "0",
        }
        for status, want in cases.items():
            self.assertEqual(self.label(status), want, "status %s" % status)

    def test_no_status_is_ever_painted_as_its_own_enum(self):
        """The bug: "CLOSED" appearing inside the countdown badge."""
        with open(LIFECYCLE, encoding="utf-8") as fh:
            names = re.findall(r'^\s+([A-Z_]+)\s*=\s*"([A-Z_]+)"',
                               fh.read(), re.M)
        self.assertTrue(names, "no RoundStatus values found")
        for _, value in names:
            out = self.label(value)
            self.assertNotIn("_", out,
                             "%s leaked a raw enum into the timer badge" % value)
            self.assertNotIn(" ", out,
                             "%s leaked a raw enum into the timer badge" % value)

    def test_an_unknown_status_degrades_to_zero_not_an_enum(self):
        self.assertEqual(self.label("SOMETHING_NEW"), "0")
        self.assertEqual(self.label(None), "0")

    def test_the_badge_never_falls_back_to_the_literal_wait(self):
        self.assertNotIn("'WAIT'", self.js,
                         "the badge must not paint a WAIT fallback")


def _node():
    for cand in ("node", "nodejs"):
        try:
            subprocess.run([cand, "--version"], capture_output=True)
            return cand
        except (OSError, FileNotFoundError):
            continue
    raise unittest.SkipTest("node is not available")


class PanelAndBannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CLIENT, encoding="utf-8") as fh:
            cls.js = fh.read()

    def test_panels_use_the_reference_stake_over_pot_header(self):
        """Reference format: "0/0" over "x2.9".

        The supplied panel art bakes exactly this, and the placement diagram
        shows it, so the "POT: / You:" labelling was reverted. The slash is only
        safe now because both operands are numbers -- this header once read
        "false/0" because it was a boolean, which is why the value, not the
        separator, is what the next test pins.
        """
        body = _grab(r"function palacePanels\(L, u\) \{.*?\n  \}", self.js)
        # Strip comments: these assertions are about code, and the comments
        # deliberately quote the old broken forms they replaced.
        body = re.sub(r"//[^\n]*", "", body)
        self.assertIn("myStake + '/' + pot", body,
                      "the panel header must be my stake over that seat's pot")
        self.assertIn("'x' + mult.toFixed(1)", body,
                      "the panel needs the multiplier")
        self.assertIn("num(S.myBets[p])", body,
                      "the stake must come from the client's own per-seat bets")
        self.assertNotIn("!!occ[p]", body,
                         "a boolean here is what produced 'false/1000'")

    def test_panel_numbers_are_coerced_not_printed_raw(self):
        body = _grab(r"function palacePanels\(L, u\) \{.*?\n  \}", self.js)
        self.assertIn("const myStake = num(", body)
        self.assertIn("const pot = num(", body)
        self.assertIn("const mult = num(", body)

    def test_the_floating_you_label_is_gone(self):
        body = _grab(r"function palacePanels\(L, u\) \{.*?\n  \}", self.js)
        self.assertNotIn("'You' : ''", body,
                         "the bare 'You' string under the panel is redundant "
                         "with the 'You: X' line inside it")

    def test_the_players_own_seat_is_marked_with_a_ring(self):
        body = _grab(r"function palaceChairs\(L, u\) \{.*?\n  \}", self.js)
        self.assertIn("active || win || mine", body,
                      "the player's seat must be ringed like an active seat")

    def test_the_winner_banner_never_covers_the_panels(self):
        """Three placements were tried and two of them hid real numbers.

        Anchored 18% into the panels band it covered all three panels; anchored
        just above the panels band it clipped the chairs. There is no free 80px
        band on a 390x844 canvas, so the badge now sits on the winning chair --
        which already carries a gold ring -- leaving the pot and the
        multiplier readable.
        """
        body = _grab(r"if \(s && s\.winners.*?requestAnimationFrame", self.js)
        self.assertNotIn("L.y.panels + L.usable * L.band.panels", body,
                         "the banner must not be drawn inside the panels band")
        self.assertNotIn("L.y.panels - bh", body,
                         "anchoring above the panels band clipped the chairs")
        self.assertIn("L.seats[s.winners[0]]", body,
                      "the badge must be anchored to the winning chair")
        self.assertIn("Math.min(150, W * 0.40", body,
                      "the badge must be a compact chair-side pill, not a "
                      "table-wide banner")


if __name__ == "__main__":
    unittest.main()


class CardArtIsWiredTest(unittest.TestCase):
    """The crops have to be reachable, not just present on disk.

    cardArt() was defined with all 52 faces on disk and never called from
    anywhere: the table painted one generic card-front with the label
    overprinted on it, so every revealed card looked identical. These tests
    execute cardArt() for every wire code the engine can deal and assert the
    file it names actually exists.
    """
    @classmethod
    def setUpClass(cls):
        with open(CLIENT, encoding="utf-8") as fh:
            src = fh.read()
        cls.js = src
        art = re.search(r"const ART_BASE = '([^']+)'", src).group(1)
        cls.art_base = art

        def grab(pat):
            m = re.search(pat, src, re.S)
            if not m:
                raise AssertionError("could not extract %r from game.js" % pat)
            return m.group(0)

        code = "\n".join([
            "const ART_BASE = %s;" % json.dumps(art),
            grab(r"const RANK_SLUG = \{.*?\};"),
            grab(r"const SUIT_SLUG = \{.*?\};"),
            "const cardArtCache = {};",
            grab(r"function cardArt\(face\) \{.*?\n  \}"),
        ])
        # Stand in for preloadImage so cardArt() returns something with a URL.
        code = code.replace("preloadImage(src)",
                            "({_src: src, complete: true,"
                            " naturalWidth: 64, naturalHeight: 64})")
        sys.path.insert(0, ROOT)
        from games.teen_patti_pro.engine import RANKS, SUITS
        probes = ["%d%s" % (r, s) for r in RANKS for s in SUITS]
        driver = (code + "\nconst probes = %s;\n"
                  "process.stdout.write(JSON.stringify(probes.map(function (f) {"
                  " const i = cardArt(f); return i && i._src ? i._src : null;"
                  " })));\n" % json.dumps(probes))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(driver)
            path = fh.name
        out = subprocess.run([_node(), path], capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError("cardArt() did not run: %s" % out.stderr)
        cls.urls = json.loads(out.stdout)
        cls.probes = probes

    def test_every_wire_code_resolves_to_a_crop(self):
        self.assertEqual(len(self.urls), 52)
        unresolved = [p for p, u in zip(self.probes, self.urls) if not u]
        self.assertEqual(unresolved, [],
                         "wire codes with no face art: %s" % unresolved)

    def test_every_named_crop_exists_on_disk(self):
        pack = os.path.join(ROOT, "assets", "games", "teen-patti-pro")
        missing = [u for u in self.urls
                   if not os.path.isfile(
                       os.path.join(pack, u.replace(self.art_base, "")))]
        self.assertEqual(missing, [], "crops named but absent: %s" % missing[:6])

    def test_face_codes_map_to_the_right_crop(self):
        """13D is a king of diamonds, not a file called card-13-diamond."""
        want = {"13D": "card-K-diamond.png", "11C": "card-J-club.png",
                "12H": "card-Q-heart.png", "14S": "card-A-spade.png",
                "10H": "card-10-heart.png", "2S": "card-2-spade.png"}
        for code, tail in want.items():
            url = self.urls[self.probes.index(code)]
            self.assertTrue(url.endswith(tail),
                            "%s resolved to %s, expected it to end %s"
                            % (code, url, tail))

    def test_the_crops_are_png_not_svg(self):
        for u in self.urls:
            self.assertTrue(u.endswith(".png"),
                            "the crops are raster: %s" % u)

    def test_the_back_is_the_cropped_reference_too(self):
        self.assertIn("cards/card-back-teenpatti.png", self.js)
        self.assertNotIn("card-back-teenpatti.svg", self.js,
                         "the sheet's back replaced the older placeholder")


class ChipDeliveryTest(unittest.TestCase):
    """A bet is delivered as real chip artwork, never as a coloured block.

    animateChipBet built the flying chip out of a CSS gradient disc with the
    denomination printed on top of it. That is a gradient pretending to be an
    asset, and it is exactly what the render rules forbid. The chip pack already
    carries one piece of art per denomination, so the animation now uses that
    and prints nothing over it.
    """
    def _js(self):
        with open(os.path.join(ROOT, "games", "teen_patti_pro", "client", "game.js"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_flying_chip_is_real_artwork(self):
        import re as _re
        js = self._js()
        m = _re.search(r"function animateChipBet\(fromPos, toPos, amount\) \{(.*?)\n    \}", js, _re.S)
        self.assertIsNotNone(m, "animateChipBet not found")
        body = m.group(1)
        self.assertIn("chipArt(amount)", body,
                      "the flying chip must use the chip art for its value")
        self.assertNotIn("linear-gradient", body,
                         "a gradient must not stand in for a chip asset")
        self.assertNotIn("chip.textContent", body,
                         "the value is already in the artwork; printing it on "
                         "top is a text character standing in for an asset")

    def test_a_stack_is_built_only_from_server_denominations(self):
        import re as _re
        js = self._js()
        self.assertIn("function chipsFor(amount, denoms)", js)
        self.assertIn("DENOMS", js,
                      "the stack must be built from the denominations the "
                      "server offered, not a client-invented list")
        m = _re.search(r"function chipsFor\(amount, denoms\) \{(.*?)\n  \}", js, _re.S)
        body = m.group(1)
        self.assertIn("remainder", body,
                      "an amount the ladder cannot make up must be reported, "
                      "not silently dropped")

    def test_stacks_render_from_the_server_pot(self):
        import re as _re
        js = self._js()
        m = _re.search(r"function palaceChipStacks\(L, u\) \{(.*?)\n  \}", js, _re.S)
        self.assertIsNotNone(m, "palaceChipStacks not found")
        self.assertIn("s.pots", m.group(1),
                      "a stack must be drawn from the server's per-seat pot, "
                      "never from a client-side accumulator")
        self.assertNotIn("S.myBets[p]", m.group(1),
                         "the local per-seat bet tally is not authoritative")

    def test_stacks_are_drawn_and_capped(self):
        import re as _re
        js = self._js()
        self.assertIn("palaceChipStacks(L, u);", js,
                      "the stacks must actually be in the draw order")
        self.assertIn("const STACK_CAP", js,
                      "an uncapped stack grows without bound and eats the table")
        self.assertIn("drawContain(art,", js,
                      "chips must be drawn with their own aspect preserved")
