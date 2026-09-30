"""SRS section 5 (UI-01..UI-14) and section 1 (seat artwork).

The HUD used to be entirely absent: there was no visible header, no round
number, no latency, and no loading or empty state, so a player joining a table
with no round running saw a blank canvas that was indistinguishable from a
hang. These tests pin each requirement to something concrete, because a
requirements list nobody checks is a list that quietly stops being true.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "games/teen_patti_pro/client"
JS = (CLIENT / "game.js").read_text(encoding="utf-8")
HTML = (CLIENT / "index.html").read_text(encoding="utf-8")
BOTH = (JS + HTML).lower()

# SRS section 1 (A left, B centre, C right) and the DearLive reference
# (green left, blue centre, red right) agree: A green, B blue, C red.
# This was A red / C green, with B and C also swapped in the layout.
# The chairs the client actually loads. These used to be the never-served
# client/assets/generated/seat-p{4,5,6}.svg placeholders, whose filenames and
# green/blue/red intent now live in the real, served pack under
# assets/games/teen-patti-pro/seats/.
SEAT_DIR = ROOT / "assets" / "games" / "teen-patti-pro" / "seats"
# Keys are the real, served chair assets. Values are the --dl-seat-* tokens in
# index.html: the chairs are painted art, so their internal fills are shaded
# renditions rather than the flat token values, but the token must still name
# the same colour the chair represents.
SEAT_COLOURS = {"seat-green.svg": "#22c55e", "seat-blue.svg": "#3b82f6",
                "seat-red.svg": "#ef4444"}


class UiRequirementTest(unittest.TestCase):
    def assertPresent(self, *needles):
        for n in needles:
            self.assertIn(n.lower(), BOTH, f"missing {n!r}")

    # -- UI-01 header ------------------------------------------------------
    def test_ui01_header_has_back_help_sound_settings(self):
        for control in ("hBack", "hSound", "hHelp", "hMenu"):
            self.assertIn(control, HTML, f"header control {control} missing")
        # Settings is the menu; it must be a real control, not a dead label.
        self.assertIn('aria-label="Menu"', HTML)

    def test_ui01_header_controls_are_wired_to_behaviour(self):
        for control in ("on('hBack'", "on('hSound'", "on('hHelp'", "on('hMenu'"):
            self.assertIn(control, JS, f"{control} is not wired")

    def test_ui01_header_controls_are_labelled_for_screen_readers(self):
        for el in re.findall(r'<button[^>]*id="h[A-Z][^"]*"[^>]*>', HTML):
            self.assertIn("aria-label", el, f"unlabelled control: {el[:70]}")

    def test_ui01_latency_is_measured_not_invented(self):
        self.assertPresent("setLatency", "measureLatency")
        # A latency readout with no measurement behind it is a lie with a
        # number in it.
        self.assertIn("Date.now() - t0", JS)
        self.assertIn("isNaN(ms)", JS, "unmeasured latency must render empty")

    # -- UI-02 round pill --------------------------------------------------
    def test_ui02_round_pill_shows_round_and_room(self):
        self.assertIn('id="roundNo"', HTML)
        self.assertIn('id="roomName"', HTML)
        self.assertIn("setRoundPill", JS)

    def test_ui02_round_pill_never_shows_a_stale_round(self):
        # Round numbers restart at 1; a pill that keeps the last value after a
        # reset tells the player the wrong round.
        self.assertIn("'#' + roundNo", JS)
        self.assertIn("'--'", JS, "no placeholder for an unknown round")

    # -- UI-03 timer -------------------------------------------------------
    def test_ui03_countdown_timer_exists(self):
        self.assertPresent("betting_end_at", "timer")

    def test_ui03_timer_is_server_anchored(self):
        # BR-01/BR-02: a client-side timer is a client-side guess.
        self.assertIn("skew", JS)

    # -- UI-04 seats -------------------------------------------------------
    def test_ui04_three_seats_are_drawn(self):
        self.assertIn("SEAT_ASSETS", JS)
        # These were client/assets/generated/seat-p{4,5,6}.svg, which the server
        # never served -- the asset root is /assets/games/teen-patti-pro/, so
        # every seat request 404'd and the chairs fell back to procedural grey.
        # Assert the real, served, spec-coloured chairs instead.
        for colour in ("seat-green", "seat-blue", "seat-red"):
            self.assertIn(colour, JS, f"{colour}.svg must be referenced by the client")

    def test_ui04_seats_are_visually_distinct(self):
        # All three chairs were generated from one gradient, so the seats were
        # indistinguishable. SRS section 1 fixes the order: A green, B blue,
        # C red.
        # Distinct hues, so the three chairs cannot render identically. The
        # placeholder SVGs were all generated from one gradient; the shipped
        # chairs are separate art, so assert on the names and on them being
        # different files.
        seen = set()
        for name, colour in SEAT_COLOURS.items():
            svg = (SEAT_DIR / name).read_text()
            self.assertTrue(svg.strip().startswith(("<svg", "<?xml")),
                            f"{name} is not an SVG")
            seen.add((SEAT_DIR / name).read_bytes())  # noqa: B018
        self.assertEqual(len(seen), 3, "the three chair assets must differ")

    def test_ui04_seat_svgs_exist_and_are_served(self):
        for name in SEAT_COLOURS:
            self.assertTrue((SEAT_DIR / name).is_file(), name)

    def test_ui04_theme_tokens_agree_with_the_svgs(self):
        # A mismatch here is invisible until someone looks at the screen.
        a, b, c = (SEAT_COLOURS["seat-green.svg"],
                   SEAT_COLOURS["seat-blue.svg"], SEAT_COLOURS["seat-red.svg"])
        for token, colour in (("--dl-seat-a", a), ("--dl-seat-b", b),
                              ("--dl-seat-c", c)):
            self.assertIn(f"{token}:{colour}", HTML, token)

    def test_ui04_seat_order_is_left_centre_right(self):
        # SRS section 1: A (left), B (centre), C (right), in ONE row.
        #
        # The layout used to place them at three unrelated heights (A and C low,
        # B high) because each was anchored separately against H, and the
        # positions were expressed as literal `pp` arrays. It is now three equal
        # columns of one row, so assert that structure: POS order, one shared y
        # for all three, and column centres that increase left to right.
        import re as _re
        m = _re.search(r"POS\.forEach\(function \(p, i\) \{([\s\S]*?)\}\);", JS)
        self.assertIsNotNone(m, "could not find the seat placement loop")
        body = m.group(1)
        self.assertIn("colW", body,
                      "seats must be laid out in equal columns, not literal offsets")
        self.assertIn("i + 0.5", body,
                      "seat i must sit in the centre of column i")
        # One y for every seat: a single band term, not a per-seat offset.
        # (The palace grid calls the band `chairs`; the old felt grid called it
        # `seats`.) What matters is one shared band anchor, no viewport-height
        # anchoring, and the three columns increasing left to right.
        self.assertTrue("y.chairs" in body or "y.seats" in body,
                        "seat y must come from one shared grid band")
        self.assertNotIn("H *", body,
                         "seat y must not be anchored to viewport height; that is "
                         "what put the three seats at three different heights")
        self.assertEqual(JS.count("colW = (W - SAFE.l - SAFE.r) / 3"), 1,
                         "exactly one column-width definition")

    def test_ui05_three_cards_per_seat(self):
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG.cards_per_hand, 3)
        # The renderer hardcodes a 3-wide fan, so a config change to another
        # hand size would silently overlap. Assert the two agree.
        self.assertPresent("hands")
        self.assertIn("3", JS, "renderer assumes a 3-card hand")

    def test_ui06_pot_per_seat_and_total(self):
        self.assertPresent("pot_total", "my_bet")

    def test_ui06_uses_the_fr06_label_wording(self):
        # FR-06 names these "Total Bet" and "My Total Bet". The client used to
        # abbreviate them to "POT"/"YOU", matching neither the spec nor the
        # reference.
        #
        # Matched case-insensitively. The placement reference renders "Total
        # Bet: 0" and "My total bet: 0" -- the second is lower case -- so
        # pinning the capital T asserted a string the reference does not
        # contain. The requirement is that both labels are present and spelled
        # out, not which case a letter is in.
        low = JS.lower()
        self.assertIn("total bet", low)
        self.assertIn("my total bet", low)
        for abbreviated in ('"POT"', "'POT'"):
            self.assertNotIn(abbreviated, JS,
                             "the pot label must not be abbreviated to POT")

    # -- UI-07 chip bar ----------------------------------------------------
    def test_ui07_chip_denominations_come_from_config(self):
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        self.assertIn("DENOMS", JS)
        for d in DEFAULT_CONFIG.denoms:
            self.assertIn(str(d), JS, f"denomination {d} not offered")

    # -- UI-08 balance -----------------------------------------------------
    def test_ui08_balance_is_shown(self):
        self.assertPresent("balance")

    # -- UI-09 repeat bet --------------------------------------------------
    def test_ui09_repeat_bet_exists(self):
        self.assertPresent("repeat")

    # -- UI-10 result overlay ---------------------------------------------
    def test_ui10_result_overlay_and_winner_highlight(self):
        self.assertPresent("winner", "winners")

    # -- UI-11 connection indicator ----------------------------------------
    def test_ui11_connection_indicator_distinguishes_states(self):
        for state in ("live", "polling", "offline", "connecting"):
            self.assertIn(f"'{state}'", JS, f"no {state} connection state")
        self.assertIn('data-conn="connecting"', HTML)

    def test_ui11_a_dropped_socket_is_not_reported_as_offline(self):
        # Polling keeps the table playable during a socket drop, so telling the
        # player they are offline would be wrong as well as alarming.
        self.assertIn("S._everConnected", JS)
        self.assertIn("setConnection('offline'", JS)

    def test_ui11_the_hud_and_the_canvas_agree_on_the_state(self):
        # Two indicators on one screen that disagree about the connection is
        # worse than either word alone, so the HUD reuses the canvas's wording.
        self.assertIn("POLLING", JS, "canvas no longer renders a polling state")
        self.assertIn("setConnection('polling'", JS)

    def test_ui11_polling_is_reported_when_there_is_no_socket(self):
        # Previously the no-WebSocket path left the HUD on "connecting" while
        # the table was in fact fully playable over polling.
        self.assertIn("setConnection('polling', 'polling')", JS)

    # -- UI-12 states ------------------------------------------------------
    def test_ui12_has_loading_empty_and_error_states(self):
        self.assertIn('id="veil"', HTML)
        for kind in ("loading", "empty", "error"):
            self.assertIn(f"'{kind}'", JS, f"no {kind} state")

    def test_ui12_error_state_offers_a_retry(self):
        # A dead end with no action is the worst of the three states.
        self.assertIn("veilBtn", JS)
        self.assertIn("onRetry", JS)

    def test_ui12_empty_state_distinguishes_from_loading(self):
        self.assertIn("setVeilForState", JS)
        self.assertIn("Waiting for players", JS)

    def test_ui12_veil_is_announced(self):
        self.assertIn('aria-live="polite"', HTML)

    # -- UI-13 reduced motion ---------------------------------------------
    def test_ui13_reduced_motion_is_respected(self):
        self.assertIn("prefers-reduced-motion", HTML)
        self.assertIn("prefers-reduced-motion", JS)

    def test_ui13_reduced_motion_covers_new_hud_motion(self):
        # The HUD added two animations. Reduced motion has to neutralise both.
        # The @keyframes rules correctly live outside the media query -- it is
        # the animation property that has to be overridden.
        # Scan every reduced-motion block rather than a fixed 400-char window
        # from the first occurrence. The window assumed this block was the first
        # one in the file, which stopped being true when the loading clip added
        # its own rule -- the assertion then silently inspected the wrong block.
        blocks = [b[:600] for b in HTML.split("prefers-reduced-motion")[1:]]
        self.assertTrue(blocks, "no reduced-motion block in the document")
        region = "\n".join(blocks).replace(" ", "")
        self.assertIn("spin", region, "HUD spinner not covered")
        self.assertIn("animation:none", region,
                      "connection blink must be disabled, not just slowed")
        # The preloader that briefly needed covering here was removed again --
        # a 1536x1024 clip cover-fit into a portrait phone dominates the view.
        # No video ships, so there is nothing left to neutralise.
        for name in ("@keyframes spin", "@keyframes blink"):
            self.assertIn(name, HTML, f"{name} missing")

    # -- UI-14 responsive --------------------------------------------------
    def test_ui14_has_breakpoints(self):
        self.assertIn("@media", HTML)

    def test_ui14_covers_360_to_1440(self):
        self.assertIn("max-width:420px", HTML, "no small-phone breakpoint")
        self.assertIn("min-width:1280px", HTML, "no large-screen breakpoint")

    def test_ui14_viewport_meta_present(self):
        self.assertIn("width=device-width", HTML)
        self.assertIn("viewport-fit=cover", HTML)

    def test_ui14_layout_is_resolution_independent(self):
        # The canvas normalises to 0..1, which is what actually satisfies
        # responsiveness for a canvas game; the CSS breakpoints only stop the
        # HUD crowding a small phone.
        self.assertIn("function layout()", JS)
        self.assertIn("normalized 0..1", JS)


class HudWiringTest(unittest.TestCase):
    """Every id the client looks up must exist in the page.

    This is the substitute for a browser render, which this environment cannot
    do: Chrome SIGTRAPs here, so the HUD has not been visually verified. A
    getElementById that returns null is the most likely way the new HUD breaks,
    and it is fully checkable statically.
    """

    def test_every_looked_up_id_exists_in_the_page(self):
        ids = set(re.findall(r'id="([^"]+)"', HTML))
        wanted = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", JS))
        # Ids the client creates at runtime are legitimately absent from the
        # static page; it looks them up defensively and creates them if missing.
        runtime = set(re.findall(r"\.id = ['\"]([^'\"]+)['\"]", JS))
        missing = sorted(w for w in wanted if w not in ids and w not in runtime)
        self.assertEqual(missing, [],
                         f"game.js looks up ids that are not in index.html: {missing}")

    def test_runtime_created_ids_are_guarded(self):
        """A dynamically created element that is fetched without a null check
        would throw on the first frame it is missing."""
        for element_id in set(re.findall(r"\.id = ['\"]([^'\"]+)['\"]", JS)):
            # Capture the variable the lookup is assigned to; the guard is on
            # that variable, not on the id string.
            m = re.search(r"const (\w+) = document\.getElementById\(['\"]"
                          + re.escape(element_id) + r"['\"]\)", JS)
            self.assertIsNotNone(
                m, f"{element_id} is created but never fetched back")
            self.assertRegex(JS, rf"if \(!{m.group(1)}\)",
                             f"{element_id} is fetched but never null-checked")

    def test_the_hud_elements_are_all_present(self):
        for el in ("hBack", "hSound", "hHelp", "hMenu", "roundPill", "roundNo",
                   "roomName", "connPill", "connText", "latency", "veil",
                   "veilTitle", "veilText", "veilSpin", "veilBtn", "c", "err",
                   "live", "kbhint"):
            self.assertIn(f'id="{el}"', HTML, f"#{el} missing from the page")

    def test_hud_elements_are_inside_the_body(self):
        body = HTML.split("<body", 1)[1]
        self.assertIn('class="hud"', body, "the header is not in the body")
        for el in ("veil", "c"):
            self.assertIn(f'id="{el}"', body, f"#{el} is not in the body")

    def test_only_one_canvas_and_it_is_the_game_surface(self):
        self.assertEqual(HTML.count("<canvas"), 1)
        self.assertIn('id="c"', HTML)

    def test_no_duplicate_ids(self):
        ids = re.findall(r'id="([^"]+)"', HTML)
        dupes = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(dupes, set(), f"duplicate ids: {sorted(dupes)}")

    def test_client_and_page_use_the_same_asset_paths(self):
        # game.js loads "assets/..."; the page is served at /teen-patti-pro/, so
        # a leading slash would break it and a missing one is correct.
        self.assertNotIn("src=\"/assets/", JS)
        self.assertRegex(JS, r"['\"]assets/", "no relative assets/ path")


class RemovedGameAssetTest(unittest.TestCase):
    """Assets for removed games and removed betting features must not ship."""

    def test_no_assets_for_removed_games_or_features(self):
        generated = CLIENT / "assets" / "generated"
        # The directory is gone: its last three members (seat-p4/5/6) were
        # never served by the API -- the asset root is /assets/games/
        # teen-patti-pro/ -- and the client now draws the real spec chairs from
        # seats/seat-{green,blue,red}.svg instead. Tolerate its absence.
        names = [p.name for p in generated.iterdir()] if generated.is_dir() else []
        for gone in ("greedy-monkey", "baby-king", "btn-blind", "btn-chaal",
                     "btn-pack", "btn-show", "btn-sideshow"):
            offenders = [n for n in names if gone in n]
            self.assertEqual(offenders, [],
                             f"{gone} is a removed feature but {offenders} shipped")

    def test_every_shipped_generated_asset_is_referenced(self):
        # An unreferenced asset is dead weight in a package with a size budget.
        referenced = set(re.findall(r"generated/([\w.-]+\.svg)", JS))
        referenced |= set(re.findall(r"generated/([\w.-]+\.svg)",
                                     (CLIENT / "assets.json").read_text()))
        generated = CLIENT / "assets" / "generated"
        for p in (generated.iterdir() if generated.is_dir() else []):
            self.assertIn(p.name, referenced,
                          f"{p.name} is shipped but nothing loads it")


def _code_only(text: str) -> str:
    """Strip JS/CSS comments.

    The client carries a comment explaining that Blind/Chaal/Pack/Show/Sideshow
    are deliberately not drawn. That comment is the documentation of an absent
    feature, not a reference to it, so it must not fail this check.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", " ", text)
    return re.sub(r"(?m)^\s*<!--.*?-->\s*$", " ", text, flags=re.S)


class NoRemovedFeatureInClientTest(unittest.TestCase):
    CODE = _code_only(JS) + _code_only(HTML)

    def test_client_does_not_implement_removed_betting_actions(self):
        for gone in ("blind", "chaal", "pack", "sideshow"):
            self.assertNotIn(gone, self.CODE.lower(),
                             f"client still implements {gone}")

    def test_the_client_documents_why_those_actions_are_absent(self):
        # The comment explaining the omission is worth keeping, and worth
        # asserting: it is the only thing stopping someone re-adding the
        # buttons a year from now.
        self.assertIn("BLIND", JS)
        self.assertIn("deliberately", JS)

    def test_client_does_not_mention_removed_games(self):
        for gone in ("greedy", "monkey", "baby king", "wheel"):
            self.assertNotIn(gone, self.CODE.lower(),
                             f"client still references {gone}")


if __name__ == "__main__":
    unittest.main()


class CardFanGeometryTest(unittest.TestCase):
    """A seat's cards must be a fan centred on that seat, inside its column.

    Regression: the hand was drawn from `pt.x - L.cw*1.15`, stepping by
    `L.cw + 5` -- anchored to the LEFT of the seat rather than around it. At
    360px that laid nine cards from x=2 to x=403: a single strip across the
    whole viewport, overlapping the neighbouring seat, running off the right
    edge and covering the chairs. The card size was also a flat 0.42 of the
    column, which does not leave room for a three-card fan.
    """

    VIEWPORTS = ((360, 800), (390, 844), (412, 915), (430, 932))

    @staticmethod
    def _geometry(w):
        col_w = w / 3
        cw = max(24, min(34, col_w * 0.235))
        gap = cw * 0.14
        total = 3 * cw + 2 * gap                # full 3-card hand width
        return col_w, cw, gap, total

    def test_cards_are_centred_on_the_seat_not_anchored_left(self):
        for w, _h in self.VIEWPORTS:
            with self.subTest(width=w):
                self.assertNotIn("L.cw * 1.15", JS,
                                 "the hand is anchored left of the seat again")
                self.assertIn("const x0 = pt.x - totalW / 2;", JS,
                              "the hand must be centred on the seat centre")
                self.assertIn("const totalW = n * L.cw + (n - 1) * gap;", JS)

    def test_fan_stays_inside_the_viewport(self):
        for w, _h in self.VIEWPORTS:
            with self.subTest(width=w):
                _col, _cw, _gap, total = self._geometry(w)
                left = (w / 3) / 2 - total / 2
                right = w - (w / 3) / 2 + total / 2
                self.assertGreaterEqual(left, 0,
                                        f"leftmost card clips at {w}px")
                self.assertLessEqual(right, w,
                                     f"rightmost card overflows at {w}px")

    def test_card_size_fits_the_column(self):
        """The whole 3-card hand must fit inside its own column."""
        for w, _h in self.VIEWPORTS:
            with self.subTest(width=w):
                col_w, cw, _gap, total = self._geometry(w)
                self.assertLessEqual(total, col_w + 0.01,
                                     f"hand wider than its column at {w}px")

    def test_card_keeps_a_card_aspect_ratio(self):
        _col, cw, _s, _f = self._geometry(360)
        self.assertAlmostEqual(cw * 1.42 / cw, 1.42, places=5)
        self.assertGreater(cw, 24, "cards must stay legible")

    def test_card_band_sits_below_the_hud(self):
        """Cards must never enter the toolbar band."""
        for w, h in self.VIEWPORTS:
            with self.subTest(size=f"{w}x{h}"):
                # The cards band is anchored to the grid, so these weights only
                # have to place it below the toolbar and above the chairs --
                # asserted structurally rather than by literal, because the
                # weights are tuned to the reference and change with it.
                m = re.search(r"w = \{([\s\S]*?)\};", JS)
                self.assertIsNotNone(m, "band weight table not found")
                wt = {k: float(v) for k, v
                      in re.findall(r"(\w+):\s*([\d.]+)", m.group(1))}
                for band in ("toolbar", "timer", "cards", "total", "chairs",
                             "panels", "bottom"):
                    self.assertIn(band, wt)
                    self.assertGreater(wt[band], 0,
                                       f"band {band} has no height")
                # Walk the grid the way palaceLayout does and assert the hand
                # ends above the chairs start. A weight table alone cannot
                # promise that -- the bands are cumulative -- so this is the
                # check that actually means "cards never sit on a chair".
                order = ["toolbar", "timer", "cards", "total", "chairs",
                         "panels", "bottom"]
                total = sum(wt[b] for b in order)
                acc, edges = 0.0, {}
                for band in order:
                    edges[band] = (acc, acc + wt[band] / total)
                    acc += wt[band] / total
                self.assertLessEqual(
                    edges["cards"][1], edges["chairs"][0] + 1e-9,
                    "the card band must end where the chair band begins")
                self.assertGreaterEqual(
                    edges["toolbar"][1], edges["timer"][0] - 1e-9,
                    "cards must never enter the toolbar band")
                self.assertIn("L.y.cards", JS,
                              "cards must be anchored to the grid, not the top")


class SingleToolbarAndIconTest(unittest.TestCase):
    """One toolbar, and no font-dependent glyphs.

    The canvas painted its own Back / Help / Sound / Menu strip at SAFE.l+24,
    directly behind the real DOM buttons, and every screenshot showed the pair
    as faded ghost icons. The DOM row is the only toolbar now; S._ctl stays
    empty so the canvas hit-test finds nothing there.

    HIST also shipped as a raw Unicode codepoint (&#9646;) that Android's font
    stack has no glyph for, so it rendered as a tofu box. The button now uses
    the supplied ui/btn-history.svg, like every other control.
    """

    def test_canvas_does_not_paint_a_control_strip(self):
        # The palace renderer owns the whole frame on the canvas, so there is
        # exactly ONE toolbar and it is this one -- no DOM strip behind it.
        # The guard that matters is that the two never both paint.
        code = JS
        self.assertIn("S._ctl = [];", code)
        self.assertNotIn("S._ctl.push({ act: c[1]", code,
                         "a second control strip is registering hit targets")

    def test_hist_uses_supplied_art_not_a_unicode_glyph(self):
        html = (CLIENT / "index.html").read_text(encoding="utf-8")
        i = html.index('id="hHist"')
        tag = html[html.rindex("<button", 0, i):html.index("</button>", i)]
        self.assertNotIn("&#", tag,
                         "the HIST button must not depend on a font glyph")
        # the art pair must include hHist so applyButtonArt styles it
        self.assertIn("['hHist', 'btnHistory']", JS)
        self.assertIn("ui/btn-history.svg", JS)
        art = (Path(__file__).resolve().parents[1]
               / "assets/games/teen-patti-pro/ui/btn-history.svg")
        self.assertTrue(art.is_file(), "btn-history.svg missing from the pack")

    def test_every_hud_button_uses_supplied_art(self):
        for el, key in (("hBack", "btnBack"), ("hHist", "btnHistory"),
                        ("hHelp", "btnHelp"), ("hSound", "btnSoundOn"),
                        ("hMenu", "btnSettings")):
            with self.subTest(el=el):
                self.assertIn(f"['{el}', '{key}']", JS)

    def test_cards_sit_above_the_chair_back(self):
        import re as _re
        # Palace layout: the hand occupies the `cards` band and the chairs the
        # `chairs` band below it, so the hand can never overlap the upholstery.
        # Assert the grid really orders them that way.
        self.assertIn("y.cards", JS, "hand must be placed from the cards band")
        self.assertIn("y.chairs", JS, "chairs must be placed from the chairs band")
        # The hand is positioned as a fraction of its band, not at a literal
        # pixel offset. A hardcoded "L.y.cards + 4" anchored the cards to the
        # top edge of a band 145px tall, leaving ~95px of dead space between
        # the cards and the chairs -- which is what read as "floating too
        # high" -- and it could not survive a different canvas height.
        self.assertRegex(JS, r"L\.y\.cards\s*\+\s*L\.usable\s*\*\s*L\.band\.cards",
                         "the hand must be placed a fraction of the way down "
                         "its band, not at the band's top edge")
        m = _re.search(r"w = \{([\s\S]*?)\};", JS)
        self.assertIsNotNone(m, "band weight table not found")
        weights = m.group(1)
        # Reference order, top to bottom: toolbar, timer, cards, total-bet,
        # chairs, betting panels, chip bar. There is no round/status band --
        # the reference carries round and room in the toolbar pill, and the
        # separate row duplicated it while pushing the table down. This was
        # asserted against the old list, so removing the row failed the test
        # that was meant to protect the layout.
        order = [k for k in ("toolbar", "timer", "cards", "total",
                             "chairs", "panels", "bottom") if k + ":" in weights]
        self.assertEqual(order, ["toolbar", "timer", "cards", "total",
                                 "chairs", "panels", "bottom"],
                         "palace bands must be declared in reference order")
        self.assertNotIn("roundbar", weights,
                         "the reference has no separate round/status row")
        # and the hand is drawn from the cards band, not the seat centre.
        # The offset is band-relative; see the assertion above.
        self.assertNotIn("L.seats[p].y -", JS.split("function palaceCards")[1][:600],
                         "the hand must not be positioned off the seat centre")

    def test_toolbar_never_wraps(self):
        """Guards a fix that was silently lost once.

        The HUD's no-wrap rule was reverted when index.html was checked out
        wholesale during an unrelated edit, and the toolbar quietly went back to
        wrapping onto a second row at 360-430px. It is the kind of CSS that
        looks fine at 390 and breaks at 360, so it is pinned here.
        """
        html = (CLIENT / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("flex-wrap:wrap", html,
                         "the HUD must not wrap its control groups")
        self.assertIn(".hud-left{flex:1 1 auto;min-width:0;overflow:hidden}", html)
        self.assertIn("#roundPill{flex:1 1 auto;min-width:0", html,
                      "round/room is the only flexible element; the "
                      "86px floor overlapped the status pill")
        self.assertIn("#hHist{flex:0 0 auto", html,
                      "history needs a fixed slot, never a shrinking one")

    def test_history_control_matches_its_asset_aspect(self):
        """btn-history.svg is a square icon, matching its slot.

        The buttons are 38x38 square slots painted with background-size:contain.
        A 4:1 pill forced into that slot scaled down to an unreadable smudge that
        read as a tofu box. The history control now has its own pill slot, and
        the other icons are all genuinely square.
        """
        import re as _re
        root = Path(__file__).resolve().parents[1]
        ui = root / "assets/games/teen-patti-pro/ui"
        hist = (ui / "btn-history.svg").read_text(encoding="utf-8")
        vb = _re.search(r'viewBox="0 0 (\d+) (\d+)"', hist)
        self.assertIsNotNone(vb)
        w, h = int(vb.group(1)), int(vb.group(2))
        # btn-history.svg was replaced with a 192x192 square icon, so the wide
        # .pillbtn slot was wrong for it -- a 4:1 pill squeezed into a square
        # slot (or vice versa) is what read as a tofu box. Both buttons are
        # square icons now and share the .iconbtn slot. The test's own message
        # asked for exactly this revisit.
        self.assertAlmostEqual(w / h, 1.0, places=1,
                               msg="btn-history should now be a square icon; "
                                   "if it changed again, revisit its CSS slot")
        for square in ("btn-help", "btn-settings", "btn-back"):
            with self.subTest(asset=square):
                vb2 = _re.search(r'viewBox="0 0 (\d+) (\d+)"',
                                 (ui / f"{square}.svg").read_text(encoding="utf-8"))
                self.assertIsNotNone(vb2)
                self.assertAlmostEqual(int(vb2.group(1)) / int(vb2.group(2)),
                                       1.0, places=1,
                                       msg=f"{square} should be a square icon")
        html = (CLIENT / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="iconbtn" id="hHist"', html,
                      "history must use the square slot its new asset needs")
        self.assertNotIn('class="pillbtn" id="hHist"', html,
                         "no element may still use the retired pill slot")


class BalanceFormatTest(unittest.TestCase):
    """The coin pill must never read "10,00K".

    fmtCompact used toFixed(2) unconditionally, so a 10,000 demo balance
    rendered as "10.00K" -- and at phone size, in the condensed balance font,
    the full stop reads as a comma. Decimals are now shown only when there
    are decimals.
    """
    def _fmt(self, src):
        import re as _re
        m = _re.search(r"function fmtCompact\(n\) \{(.*?)\n  \}", src, _re.S)
        self.assertIsNotNone(m, "fmtCompact not found")
        return m.group(1)

    def test_whole_thousands_have_no_decimals(self):
        body = self._fmt(JS)
        self.assertIn(".replace(/\\.00$/, '')", body,
                      "a whole number of thousands must drop its decimals")
        self.assertNotIn("toFixed(2) + 'K'", body,
                         "the unconditional toFixed(2) is what produced 10.00K")

    def test_the_coin_pill_uses_the_compact_formatter(self):
        self.assertRegex(JS, r"fmtCompact\(num\(S\.balance\)\)",
                         "the balance pill must go through fmtCompact")
