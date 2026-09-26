"""BRD/SRS requirements that were declared but not implemented.

Every test here corresponds to a compliance FAIL found in the v1.0.0 audit:
a config field or store method that existed, looked real, and did nothing.
They are grouped in one file because the failure mode is shared -- a field
that is declared and never read -- and that is worth testing as a category.
"""
import dataclasses
import unittest

from common.wallet import MemoryWallet
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.engine import (HAND_CATEGORIES, evaluate_hand,
                                         rank_scale, score_hand)
from games.teen_patti_pro.service import TeenPattiService


def _card(rank, suit):
    return (rank, suit)


TRAIL = [_card(13, "s"), _card(13, "h"), _card(13, "d")]
HIGH = [_card(2, "s"), _card(9, "h"), _card(13, "d")]
PAIR = [_card(13, "s"), _card(13, "h"), _card(7, "c")]
COLOR = [_card(5, "s"), _card(9, "s"), _card(13, "s")]
SEQ = [_card(12, "s"), _card(13, "h"), _card(14, "d")]
PURE_SEQ = [_card(12, "s"), _card(13, "s"), _card(14, "s")]

# trail beats everything: the documented default, weakest category first.
DEFAULT = ("high", "pair", "color", "seq", "pure_seq", "trail")
# An admin order that inverts trail and high.
INVERTED = ("trail", "pure_seq", "seq", "color", "pair", "high")


def _settle_with_hands(cap=0, rake_bps=0, stakes=(20, 1000),
                       hands=None, ranking_order=DEFAULT):
    """Settle a round with the hands forced, so results are deterministic.

    The engine seeds each round from `secrets`, so which seat holds the winning
    hand varies per run. A test that asserts on a cap or a payout without
    fixing the hands is testing the shuffle, not the rule.
    """
    hands = hands or {"A": [_card(13, "s"), _card(13, "h"), _card(13, "d")],
                      "B": [_card(2, "s"), _card(9, "h"), _card(7, "d")]}
    w = MemoryWallet()
    for pid in ("a", "b"):
        w.fund(pid, 200_000)
    cfg = dataclasses.replace(TeenPattiConfig(confirmed=True), max_win_cap=cap,
                              rake_bps=rake_bps, jokers=0,
                              ranking_order=ranking_order)
    svc = TeenPattiService(config=cfg, wallet=w)
    svc.start_round("r")
    svc.place_bet("r", "a", "A", stakes[0], "k1")
    svc.place_bet("r", "b", "B", stakes[1], "k2")
    svc.close_betting("r")
    rnd = svc.rooms["r"].round
    rnd.hands = {k: list(v) for k, v in hands.items()}
    rnd.resolved = {k: list(v) for k, v in hands.items()}
    out = svc.publish_result("r")
    return out, svc.settle("r")["settlements"], rnd

class Br11ConfigurableRankingsTest(unittest.TestCase):
    """BR-11: hand rankings admin-configurable.

    Was a FAIL: `ranking_order` was declared in config and referenced nowhere,
    so `evaluate_hand` returned hardcoded 6/5/4/3/2/1 and an operator could
    reorder the ranking with no effect at all.
    """

    def test_default_order_is_unchanged(self):
        # The fix must not alter shipped behaviour.
        self.assertEqual(evaluate_hand(TRAIL)[0], 6)
        self.assertEqual(evaluate_hand(PURE_SEQ)[0], 5)
        self.assertEqual(evaluate_hand(SEQ)[0], 4)
        self.assertEqual(evaluate_hand(COLOR)[0], 3)
        self.assertEqual(evaluate_hand(PAIR)[0], 2)
        self.assertEqual(evaluate_hand(HIGH)[0], 1)

    def test_explicit_default_order_matches_the_implicit_one(self):
        for hand in (TRAIL, PURE_SEQ, SEQ, COLOR, PAIR, HIGH):
            self.assertEqual(evaluate_hand(hand),
                             evaluate_hand(hand, ranking_order=DEFAULT), hand)

    def test_an_admin_order_actually_changes_the_winner(self):
        self.assertGreater(evaluate_hand(TRAIL)[0], evaluate_hand(HIGH)[0])
        self.assertLess(evaluate_hand(TRAIL, ranking_order=INVERTED)[0],
                        evaluate_hand(HIGH, ranking_order=INVERTED)[0])

    def test_rank_scale_is_weakest_first(self):
        scale = rank_scale(DEFAULT)
        self.assertEqual(scale["high"], 1)
        self.assertEqual(scale["trail"], 6)

    def test_unknown_category_is_rejected_loudly(self):
        with self.assertRaises(ValueError) as cm:
            rank_scale(("high", "pair", "quads"))
        self.assertIn("quads", str(cm.exception))

    def test_duplicate_category_is_rejected(self):
        with self.assertRaises(ValueError):
            rank_scale(("high", "high", "pair"))

    def test_tiebreak_is_unaffected_by_ordering(self):
        # The tiebreak is a property of the cards, not of the ranking order.
        self.assertEqual(evaluate_hand(TRAIL)[1], evaluate_hand(TRAIL, ranking_order=INVERTED)[1])

    def test_score_hand_honours_a_custom_order(self):
        # score_hand is the authoritative scorer and had a precomputed table
        # baked with the default order; a custom order must bypass it.
        self.assertEqual(score_hand(TRAIL, ranking_order=INVERTED)[0], 1)
        self.assertEqual(score_hand(TRAIL)[0], 6)
        self.assertEqual(score_hand(TRAIL, ranking_order=DEFAULT)[0], 6)

    def test_settlement_uses_the_configured_order(self):
        """End to end: the same two hands, two rankings, two different winners.

        Two independent services rather than mutating one round between
        publishes -- re-publishing a round that has already advanced its status
        tests the status machine, not the ranking.
        """
        # A holds a trail, B holds a high card.
        hands = {"A": [_card(13, "s"), _card(13, "h"), _card(13, "d")],
                 "B": [_card(2, "s"), _card(9, "h"), _card(7, "d")]}
        default_result, _, _ = _settle_with_hands(hands=hands,
                                                  ranking_order=DEFAULT)
        inverted_result, _, _ = _settle_with_hands(hands=hands,
                                                  ranking_order=INVERTED)
        self.assertEqual(default_result["winners"], ["A"],
                         "a trail beats a high card by default")
        self.assertEqual(inverted_result["winners"], ["B"],
                         "inverting the ranking must hand the win to B")


class Br14MaxWinCapTest(unittest.TestCase):
    """BR-14: max win cap configurable.

    Was a FAIL: `max_win_cap` existed with the comment "100 = 100x bet" but
    nothing read it, so an operator who set a cap had no cap.
    """

    # A holds a trail, B holds a high card, so A always wins and the cap is
    # measured against A's own (small) stake.
    A_TRAIL = {"A": [_card(13, "s"), _card(13, "h"), _card(13, "d")],
               "B": [_card(2, "s"), _card(9, "h"), _card(7, "d")]}

    def _settle(self, cap, a_stake, b_stake):
        _, rows, rnd = _settle_with_hands(cap=cap, stakes=(a_stake, b_stake),
                                          hands=self.A_TRAIL)
        return rows, rnd

    def test_no_cap_when_zero(self):
        rows, rnd = self._settle(0, 20, 1000)
        paid = [r["payout"] for r in rows if r["payout"] > 0]
        self.assertEqual(sum(paid), 1020, "no cap means the whole pot pays out")
        self.assertEqual(rnd.carry_out, 0)
        self.assertEqual(rnd.cap_binds, [])

    def test_cap_clamps_a_win_to_the_configured_multiple(self):
        rows, rnd = self._settle(2, 20, 1000)
        paid = [r["payout"] for r in rows if r["payout"] > 0]
        self.assertEqual(paid, [40], "2x the 20 stake is the ceiling")
        self.assertEqual(len(rnd.cap_binds), 1)
        self.assertEqual(rnd.cap_binds[0]["uncapped_payout"], 1020)
        self.assertEqual(rnd.cap_binds[0]["cap_limit"], 40)

    def test_clawed_back_money_is_carried_not_destroyed(self):
        # The cap must not create or destroy money: the excess goes to
        # carry_out so the books still balance.
        rows, rnd = self._settle(2, 20, 1000)
        paid = sum(r["payout"] for r in rows)
        self.assertEqual(paid + rnd.carry_out, 1020,
                         "paid + carried must equal the pot")

    def test_settlement_rows_report_whether_the_cap_bound(self):
        rows, _ = self._settle(2, 20, 1000)
        capped = [r for r in rows if r.get("cap_applied")]
        self.assertEqual(len(capped), 1)
        self.assertEqual(capped[0]["payout"], 40)

    def test_cap_does_not_bind_when_the_win_is_within_it(self):
        rows, rnd = self._settle(100, 20, 1000)
        self.assertEqual(sum(r["payout"] for r in rows), 1020)
        self.assertEqual(rnd.cap_binds, [])

    def test_cap_is_idempotent_on_replay(self):
        # settle() is called again by the retry path; the cap must not apply
        # twice or produce different rows.
        w = MemoryWallet()
        for pid in ("a", "b"):
            w.fund(pid, 100_000)
        svc = TeenPattiService(
            config=dataclasses.replace(TeenPattiConfig(confirmed=True),
                                       max_win_cap=2), wallet=w)
        svc.start_round("r")
        svc.place_bet("r", "a", "A", 20, "k1")
        svc.place_bet("r", "b", "B", 1000, "k2")
        svc.close_betting("r")
        svc.publish_result("r")
        first = svc.settle("r")["settlements"]
        second = svc.settle("r")["settlements"]
        self.assertEqual(first, second)


class Br13RakeTest(unittest.TestCase):
    """BR-13: rake configurable, default 0%. Already applied; pinned so the
    default cannot drift and a non-zero rake stays covered."""

    A_TRAIL = {"A": [_card(13, "s"), _card(13, "h"), _card(13, "d")],
               "B": [_card(2, "s"), _card(9, "h"), _card(7, "d")]}

    def _rake(self, rake_bps):
        _, rows, rnd = _settle_with_hands(cap=0, rake_bps=rake_bps,
                                          stakes=(1000, 1000),
                                          hands=self.A_TRAIL)
        return rows, rnd

    def test_default_rake_is_zero(self):
        self.assertEqual(TeenPattiConfig().rake_bps, 0)
        rows, rnd = self._rake(0)
        self.assertEqual(sum(r["payout"] for r in rows), 2000)

    def test_rake_is_withheld_from_the_pot(self):
        # 500 bps = 5% of a 2000 pot = 100.
        rows, rnd = self._rake(500)
        self.assertEqual(sum(r["payout"] for r in rows), 1900)
        self.assertEqual(rnd.carry_out, 0)


class EmptySeatCannotWinTest(unittest.TestCase):
    """Found during the v1.0.0 compliance audit, not in the original spec.

    With 3 seats and fewer than 3 players, the best dealt hand could land on an
    EMPTY seat. The pot was then voided: both players lost their whole stake to
    cards nobody had bet on, and the money carried forward. Roughly 1 round in 3
    with two players.
    """

    def _play_two_players(self):
        w = MemoryWallet()
        for pid in ("a", "b"):
            w.fund(pid, 100_000)
        svc = TeenPattiService(
            config=dataclasses.replace(TeenPattiConfig(confirmed=True),
                                       max_win_cap=0), wallet=w)
        svc.start_round("r")
        svc.place_bet("r", "a", "A", 1000, "k1")
        svc.place_bet("r", "b", "B", 100, "k2")
        svc.close_betting("r")
        result = svc.publish_result("r")
        return result, svc.settle("r")["settlements"]

    def test_someone_always_wins_when_players_staked(self):
        for _ in range(15):
            result, rows = self._play_two_players()
            self.assertTrue(result["winners"],
                            "a pot with staked seats must have a winner")
            self.assertGreater(sum(r["payout"] for r in rows), 0,
                               "the pot must be paid out")

    def test_the_winner_is_always_a_seat_that_staked(self):
        for _ in range(15):
            result, _rows = self._play_two_players()
            for position in result["winners"]:
                self.assertIn(position, ("A", "B"),
                              "seat C was empty and must not win")

    def test_a_round_with_no_bets_has_no_winner(self):
        w = MemoryWallet()
        w.fund("a", 1000)
        svc = TeenPattiService(
            config=dataclasses.replace(TeenPattiConfig(confirmed=True),
                                       max_win_cap=0), wallet=w)
        svc.start_round("empty")
        svc.close_betting("empty")
        result = svc.publish_result("empty")
        self.assertEqual(result["winners"], [])


class WsBalanceUpdatedTest(unittest.TestCase):
    """SRS section 8: balance.updated. Was a FAIL -- declared in the event
    vocabulary with no emit site, so a client waiting for it waited forever."""

    def _service(self):
        w = MemoryWallet()
        for pid in ("a", "b"):
            w.fund(pid, 50_000)
        return TeenPattiService(
            config=dataclasses.replace(TeenPattiConfig(confirmed=True),
                                       max_win_cap=0), wallet=w)

    def test_bet_debit_emits_balance_updated(self):
        svc = self._service()
        svc.start_round("r")
        before = svc.wallet.get_balance("a").available
        svc.place_bet("r", "a", "A", 100, "k1")
        events = [e for e in svc.event_log if e["kind"] == "balance.updated"]
        self.assertEqual(len(events), 1, "one debit, one event")
        self.assertEqual(events[0]["data"]["player_id"], "a")
        self.assertEqual(events[0]["data"]["balance"],
                         before - 100, "must report the post-debit balance")
        self.assertEqual(events[0]["data"]["reason"], "bet_debit")

    def test_win_credit_emits_balance_updated_with_the_post_credit_value(self):
        svc = self._service()
        svc.start_round("r")
        svc.place_bet("r", "a", "A", 1000, "k1")
        svc.place_bet("r", "b", "B", 20, "k2")
        svc.close_betting("r")
        svc.publish_result("r")
        svc.settle("r")
        events = [e for e in svc.event_log if e["kind"] == "balance.updated"]
        self.assertTrue(events, "settlement must emit balance.updated")
        # Critically: the reported value is the balance AFTER the credit, so a
        # client that renders it does not show a stale figure.
        winner = [e for e in events if e["data"]["reason"] == "win_credit"]
        self.assertTrue(winner)
        for e in winner:
            self.assertEqual(e["data"]["balance"],
                             svc.wallet.get_balance(e["data"]["player_id"]).available)

    def test_every_spec_event_now_has_an_emit_site(self):
        from common.wire_events import DECLARED_ONLY, WS_EVENTS, WS_NAMES
        self.assertEqual(DECLARED_ONLY, {},
                         "an event is declared but never emitted")
        for name in WS_EVENTS:
            self.assertIn(name, WS_NAMES.values(), name)


if __name__ == "__main__":
    unittest.main()
