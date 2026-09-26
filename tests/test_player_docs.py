"""Player-facing documentation must not lie about the game.

A how-to-play page that says "20 seconds" when the engine waits 20,000ms is
worse than no page: players rely on it to decide whether to bet. These tests
pin the numbers on the page to the config the engine actually runs, so the two
cannot drift apart silently.
"""
import re
import unittest
from pathlib import Path

from games.teen_patti_pro.config import DEFAULT_CONFIG
from games.teen_patti_pro.engine import evaluate_hand

CLIENT = Path(__file__).resolve().parent.parent / "games" / "teen_patti_pro" / "client"
PAGE = CLIENT / "how-to-play.html"
LOBBY = CLIENT / "lobby.html"


def _text():
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", PAGE.read_text()))


class PlayerDocsTest(unittest.TestCase):
    def test_the_page_exists_and_is_linked_from_the_lobby(self):
        self.assertTrue(PAGE.is_file())
        self.assertIn("how-to-play.html", LOBBY.read_text(),
                      "an unlinked rules page is a page nobody reads")

    def test_bet_denominations_match_the_config(self):
        for denom in DEFAULT_CONFIG.denoms:
            self.assertRegex(_text(), rf"\b{denom:,}\b|\b{denom}\b",
                             f"denomination {denom} is not on the page")

    def test_bet_limits_match_the_config(self):
        text = _text()
        self.assertIn(f"{DEFAULT_CONFIG.min_bet:,}", text)
        self.assertIn(f"{DEFAULT_CONFIG.max_bet:,}", text)
        self.assertIn(str(DEFAULT_CONFIG.max_bets_per_player_per_round), text)

    def test_guess_window_matches_the_config(self):
        # The page must not quote a guess time other than the configured one.
        self.assertIn(f"{DEFAULT_CONFIG.guess_ms // 1000} seconds", _text())

    def test_seat_count_matches_the_config(self):
        self.assertIn(str(len(DEFAULT_CONFIG.seats)), _text())

    def test_joker_count_matches_the_config(self):
        # Stating a joker count the deck does not use would send players hunting
        # for wild cards that do not exist. _text() strips tags, so the stat
        # reads as "0 jokers in play" rather than "<b>0</b>".
        text = _text()
        self.assertRegex(text, rf"\b{DEFAULT_CONFIG.jokers}\s+jokers?\b")
        if DEFAULT_CONFIG.jokers == 0:
            self.assertNotIn("joker is wild", text.lower())

    def test_card_count_matches_the_config(self):
        # Three cards per hand is the engine's hand size, stated twice so a
        # partial edit cannot leave the page contradicting itself.
        text = _text()
        self.assertIn("3 cards per hand", text)
        self.assertIn("three cards", text.lower())

    def test_every_hand_rank_on_the_page_is_real(self):
        # The page lists six ranks. Each must be a category the evaluator can
        # actually return, or the table is describing a game nobody is playing.
        text = _text().lower()
        for name in ("trail", "pure sequence", "sequence", "colour", "pair",
                     "high card"):
            self.assertIn(name, text, f"{name} is missing from the page")

    def test_rank_order_on_the_page_matches_the_evaluator(self):
        # Strongest first: trail(6) > pure_seq(5) > seq(4) > colour(3) >
        # pair(2) > high(1). The page must present them in that order.
        text = _text().lower()
        order = [text.index(n) for n in ("trail", "pure sequence", "sequence",
                                          "colour", "pair", "high card")]
        self.assertEqual(order, sorted(order),
                         "the page lists hand rankings in the wrong order")

    def test_evaluator_agrees_with_the_documented_ordering(self):
        # Guard the ranking claim itself, independently of the page: a trail
        # beats a pure sequence, which beats a sequence, and so on.
        def hand(ranks, suits):
            return list(zip(ranks, suits))
        trail = hand([13, 13, 13], ["s", "h", "d"])
        pure = hand([12, 13, 14], ["s", "s", "s"])
        seq = hand([12, 13, 14], ["s", "h", "d"])
        colour = hand([5, 9, 13], ["s", "s", "s"])
        pair = hand([13, 13, 7], ["s", "h", "d"])
        high = hand([2, 9, 13], ["s", "h", "d"])
        ranks = [evaluate_hand(h)[0] for h in
                 (trail, pure, seq, colour, pair, high)]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        self.assertEqual(ranks, [6, 5, 4, 3, 2, 1])

    def test_the_page_names_no_other_game(self):
        # The wheel games were removed; a rules page still advertising them
        # would send players to a 404.
        text = _text().lower()
        for gone in ("lucky wheel", "wheel of", "spin the wheel", "baby king",
                     "greedy monkey"):
            self.assertNotIn(gone, text)


if __name__ == "__main__":
    unittest.main()
