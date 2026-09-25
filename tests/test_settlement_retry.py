"""Settlement-retry money-safety tests (Fix #1).

A round whose result/settlement raises must never be abandoned with an
unsettled pot. These tests pin the re-drive path, the backoff, the give-up
alert, and the no-double-credit guarantee.
"""
import unittest
from dataclasses import replace

from common.lifecycle import RoundStatus, SETTLE_MAX_ATTEMPTS, settle_backoff_ms
from common.wallet import WalletError
from games.teen_patti_pro.config import DEFAULT_CONFIG
from games.teen_patti_pro.service import TeenPattiService


class FlakyWallet:
    """Fails the first N settlement credits, then behaves normally.

    Wraps a real MemoryWallet so post-retry balances are genuinely correct
    rather than asserted against a stub.
    """

    def __init__(self, fail_credits=0):
        from common.wallet import MemoryWallet
        self.inner = MemoryWallet()
        self.fail_credits = fail_credits
        self.credit_calls = []
        self.debit_calls = []

    def fund(self, player_id, amount):
        self.inner.fund(player_id, amount)

    def get_balance(self, player_id):
        return self.inner.get_balance(player_id)

    def debit(self, player_id, amount, ref, idempotency_key):
        self.debit_calls.append((player_id, amount, idempotency_key))
        return self.inner.debit(player_id, amount, ref, idempotency_key)

    def credit(self, player_id, amount, ref, idempotency_key):
        self.credit_calls.append((player_id, amount, idempotency_key))
        if self.fail_credits > 0:
            self.fail_credits -= 1
            raise WalletError("simulated downstream outage")
        return self.inner.credit(player_id, amount, ref, idempotency_key)

    def void_debit(self, player_id, ref, idempotency_key):
        return self.inner.void_debit(player_id, ref, idempotency_key)


def _seat_round(svc, room_id, player_id, per_seat, now):
    """Seat a player and bet every seat at `per_seat`.

    The engine deals to ALL configured seats, including empty ones, so a
    single-seat bet can lose to a phantom hand. Betting all three seats makes
    the pot accounting deterministic: rake is 0 and the pro-rata dead-heat
    rule pays the whole distributable to the single winning bet, so a clean
    run always returns the player to their opening balance.
    """
    svc.sessions.create(player_id, room_id, "teen-patti-pro")
    svc._room(room_id).create_session(player_id)
    svc.start_round(room_id)
    before = svc.wallet.get_balance(player_id).available
    total = 0
    for i, seat in enumerate(svc.config.seats):
        svc.place_bet(room_id, player_id, seat, per_seat, f"k-{player_id}-{i}")
        total += per_seat
    return before, total


class SettlementRetryTest(unittest.TestCase):
    def setUp(self):
        # DEFAULT_CONFIG is frozen and confirmed=False (the TBC gate blocks
        # real money on it), so derive an independent confirmed copy.
        self.cfg = replace(DEFAULT_CONFIG, confirmed=True)
        self.opening = 10_000

    def _svc(self, wallet=None, now=1_000_000):
        svc = TeenPattiService(config=self.cfg, wallet=wallet or FlakyWallet())
        svc._now = lambda: now
        return svc

    def _fail_settlement(self, svc, room_id):
        """Drive close_betting + calculate_result, then settle expecting failure."""
        room = svc._room(room_id)
        room.close_betting(svc._now())
        room.calculate_result(svc._now())
        with self.assertRaises(WalletError):
            svc.settle(room_id, room.round.round_id)
        return room

    def _scenario(self, wallet):
        svc = self._svc(wallet)
        wallet.fund("p1", self.opening)
        _seat_round(svc, "r1", "p1", 100, svc._now())
        return svc, svc._room("r1")

    # ---- the bug: a failure used to strand the pot forever ----

    def test_failed_settle_parks_round_not_closes_it(self):
        svc, room = self._scenario(FlakyWallet(fail_credits=99))
        self._fail_settlement(svc, "r1")
        self.assertEqual(room.round.status, RoundStatus.SETTLED_PENDING)
        self.assertNotEqual(room.round.status, RoundStatus.CLOSED)
        # engine.settle() completes before the credit loop, so a credit failure
        # parks a CLOSED round: the resume point is the (idempotent) credit loop.
        self.assertEqual(room.round._settle_resume_from, RoundStatus.CLOSED.value)
        self.assertEqual(room.round._settle_attempts, 1)

    def test_parked_round_is_picked_up_by_a_later_sweep(self):
        """Core regression: the old guard skipped every non-BETTING_OPEN round."""
        svc, room = self._scenario(FlakyWallet(fail_credits=1))
        self._fail_settlement(svc, "r1")
        self.assertEqual(room.round.status, RoundStatus.SETTLED_PENDING)

        # Backoff not elapsed -> still parked, and still tracked.
        svc.sweep(svc._now())
        self.assertEqual(room.round.status, RoundStatus.SETTLED_PENDING)

        # Past backoff -> re-driven, and this time it settles.
        svc.sweep(svc._now() + settle_backoff_ms(1) + 1)
        self.assertEqual(room.round.status, RoundStatus.CLOSED)

    def test_retry_settles_and_credits_exactly_once(self):
        wallet = FlakyWallet(fail_credits=1)
        svc, room = self._scenario(wallet)
        self._fail_settlement(svc, "r1")
        staked = wallet.get_balance("p1").available
        self.assertEqual(staked, self.opening - 300)  # three seats x 100

        later = svc._now() + settle_backoff_ms(1) + 1
        svc.sweep(later)
        self.assertEqual(room.round.status, RoundStatus.CLOSED)
        settled = wallet.get_balance("p1").available
        self.assertEqual(settled, self.opening)  # full pot returned, rake 0

        # No double credit: further sweeps must not pay again.
        for step in (2000, 4000, 8000, 16000):
            svc.sweep(later + step)
        self.assertEqual(wallet.get_balance("p1").available, settled)

    def test_publish_result_failure_resumes_from_betting_closed(self):
        """A failure before RESULT must re-run calculate_result on retry."""
        svc, room = self._scenario(FlakyWallet())
        room.close_betting(svc._now())
        self.assertEqual(room.round.status, RoundStatus.BETTING_CLOSED)

        svc._park_settlement("r1", room, LifecycleErrorShim("simulated result failure"),
                             svc._now())
        self.assertEqual(room.round.status, RoundStatus.SETTLED_PENDING)
        self.assertEqual(room.round._settle_resume_from,
                         RoundStatus.BETTING_CLOSED.value)

        svc.sweep(svc._now() + settle_backoff_ms(1) + 1)
        self.assertEqual(room.round.status, RoundStatus.CLOSED)
        self.assertTrue(room.round.winner_positions)
        self.assertEqual(svc.wallet.get_balance("p1").available, self.opening)

    # ---- give-up + alerting ----

    def test_exhausts_attempts_then_alerts_and_stays_failed(self):
        wallet = FlakyWallet(fail_credits=10_000)
        svc, room = self._scenario(wallet)
        self._fail_settlement(svc, "r1")

        clock = svc._now()
        for _ in range(SETTLE_MAX_ATTEMPTS + 4):
            clock += settle_backoff_ms(1) + 1
            svc.sweep(clock)

        self.assertEqual(room.round.status, RoundStatus.SETTLE_FAILED)
        self.assertGreaterEqual(room.round._settle_attempts, SETTLE_MAX_ATTEMPTS)
        self.assertTrue(svc.settlement_alerts, "exhausted settlement must alert")
        alert = svc.settlement_alerts[-1]
        self.assertEqual(alert["round_id"], room.round.round_id)
        self.assertEqual(alert["unpaid_winnings"], 300)
        # A failed round is never silently closed.
        self.assertNotEqual(room.round.status, RoundStatus.CLOSED)

    # ---- operator surfacing ----

    def test_health_report_counts_pending_and_failed(self):
        svc, room = self._scenario(FlakyWallet(fail_credits=10_000))
        self._fail_settlement(svc, "r1")

        rep = svc.settlement_health_report(svc._now() + 5_000)
        self.assertEqual(rep["settled_pending"], 1)
        self.assertEqual(rep["settle_failed"], 0)
        self.assertEqual(rep["unpaid_winnings_total"], 300)
        self.assertEqual(rep["pending"][0]["unpaid_winnings"], 300)
        self.assertGreaterEqual(rep["oldest_pending_age_ms"], 5_000)
        self.assertEqual(rep["settle_max_attempts"], SETTLE_MAX_ATTEMPTS)
        self.assertEqual(len(rep["pending"]), 1)
        self.assertEqual(rep["pending"][0]["room_id"], "r1")

    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual([settle_backoff_ms(n) for n in range(1, 6)],
                         [1000, 2000, 4000, 8000, 16000])
        self.assertEqual(settle_backoff_ms(20), 60_000)
        self.assertEqual(settle_backoff_ms(0), 1000)

    def test_healthy_round_is_untouched_by_the_retry_machinery(self):
        wallet = FlakyWallet()
        svc, room = self._scenario(wallet)
        room.close_betting(svc._now())
        room.calculate_result(svc._now())
        svc.settle("r1", room.round.round_id)
        self.assertEqual(room.round.status, RoundStatus.CLOSED)
        self.assertEqual(room.round._settle_attempts, 0)
        self.assertEqual(wallet.get_balance("p1").available, self.opening)
        rep = svc.settlement_health_report(svc._now())
        self.assertEqual(rep["settled_pending"], 0)
        self.assertEqual(rep["unpaid_winnings_total"], 0)
        self.assertEqual(svc.settlement_alerts, [])

    def test_mark_pending_is_idempotent(self):
        """A second failure must not clobber the recorded resume point."""
        svc, room = self._scenario(FlakyWallet(fail_credits=10_000))
        self._fail_settlement(svc, "r1")
        first_resume = room.round._settle_resume_from
        first_attempts = room.round._settle_attempts
        svc._park_settlement("r1", room, WalletError("again"), svc._now() + 1)
        self.assertEqual(room.round._settle_resume_from, first_resume)
        self.assertEqual(room.round._settle_attempts, first_attempts)
        self.assertEqual(room.round.status, RoundStatus.SETTLED_PENDING)


class LifecycleErrorShim(Exception):
    pass


if __name__ == "__main__":
    unittest.main()
