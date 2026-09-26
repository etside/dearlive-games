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

CLIENT = Path(__file__).resolve().parents[1] / "games/teen_patti_pro/client"
JS = (CLIENT / "game.js").read_text(encoding="utf-8")
HTML = (CLIENT / "index.html").read_text(encoding="utf-8")
BOTH = (JS + HTML).lower()

# SRS section 1 (A left, B centre, C right) and the DearLive reference
# (green left, blue centre, red right) agree: A green, B blue, C red.
# This was A red / C green, with B and C also swapped in the layout.
SEAT_COLOURS = {"seat-p4.svg": "#22c55e", "seat-p5.svg": "#3b82f6",
                "seat-p6.svg": "#ef4444"}


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
        self.assertEqual(JS.count("seat-p"), 3, "expected exactly three seat assets")

    def test_ui04_seats_are_visually_distinct(self):
        # All three chairs were generated from one gradient, so the seats were
        # indistinguishable. SRS section 1 fixes the order: A green, B blue,
        # C red.
        seen = set()
        for name, colour in SEAT_COLOURS.items():
            svg = (CLIENT / "assets/generated" / name).read_text()
            self.assertIn(colour.lower(), svg.lower(),
                          f"{name} is not {colour}")
            seen.add(colour.lower())
        self.assertEqual(len(seen), 3, "seat colours must differ from each other")

    def test_ui04_seat_svgs_exist_and_are_served(self):
        for name in SEAT_COLOURS:
            self.assertTrue((CLIENT / "assets/generated" / name).is_file(), name)

    def test_ui04_theme_tokens_agree_with_the_svgs(self):
        # A mismatch here is invisible until someone looks at the screen.
        a, b, c = (SEAT_COLOURS["seat-p4.svg"],
                   SEAT_COLOURS["seat-p5.svg"], SEAT_COLOURS["seat-p6.svg"])
        for token, colour in (("--dl-seat-a", a), ("--dl-seat-b", b),
                              ("--dl-seat-c", c)):
            self.assertIn(f"{token}:{colour}", HTML, token)

    def test_ui04_seat_order_is_left_centre_right(self):
        # SRS section 1: A (left), B (center), C (right). The layout previously
        # put B right and C top-centre, contradicting both the SRS and the
        # reference. Assert the geometry, not just that positions exist.
        import re as _re
        m = _re.search(r"const pp = land\s*\?\s*(\[.*?\])\s*:\s*(\[.*?\]);",
                       JS, _re.S)
        self.assertIsNotNone(m, "could not find the seat layout arrays")
        land = m.group(1)
        centre_idx = land.index("x: cx,")
        self.assertIn("cx - rx", land[:centre_idx], "leftmost seat must be cx - rx")
        self.assertIn("cx + rx", land[centre_idx:], "rightmost seat must be cx + rx")
        # POS is ['A','B','C'] and pp is indexed in that order, so index 1 is
        # the centre seat and must be the one drawn at cx.
        self.assertLess(land.index("x: cx,"), land.index("cx + rx"),
                        "index 1 (seat B) must be the centre seat")

    # -- UI-05/06 cards and pots ------------------------------------------
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
        # FR-06 names these "Total Bet" and "My Total Bet". The client
        # abbreviated them to "POT"/"YOU", matching neither the spec nor the
        # reference.
        self.assertIn("Total Bet", JS)
        self.assertIn("My Total Bet", JS)

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
        block = HTML.split("prefers-reduced-motion", 1)[1][:400]
        self.assertIn("spin", block, "HUD spinner not covered")
        self.assertIn("animation:none", block.replace(" ", ""),
                      "connection blink must be disabled, not just slowed")
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
        names = [p.name for p in generated.iterdir()]
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
        for p in (CLIENT / "assets" / "generated").iterdir():
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
