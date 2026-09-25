"""Idempotency tri-state tests (Fix #2).

The old claim() contract returned None both for "freshly claimed, go execute"
and for "another worker holds this key and has not finished". Callers could not
tell a first claim from an in-flight duplicate, so a retry re-entered the money
path. These tests pin the explicit ClaimResult contract and the exactly-once
debit guarantee under concurrency.
"""
import threading
import unittest
from dataclasses import replace

from common.idempotency import (ClaimResult, IdempotencyConflict,
                                MemoryIdempotencyStore)
from common.wallet import MemoryWallet
from games.teen_patti_pro.config import DEFAULT_CONFIG
from games.teen_patti_pro.service import ServiceError, TeenPattiService, _h

HASH = "hash-1"


class ClaimContractTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryIdempotencyStore()

    def test_claim_returns_claimed_on_first_call(self):
        out = self.store.claim("k", HASH)
        self.assertIs(out.state, ClaimResult.CLAIMED)
        self.assertIsNone(out.result)

    def test_claim_returns_replay_on_duplicate_key(self):
        self.store.claim("k", HASH)
        self.store.complete("k", {"bet_id": "b1"})
        out = self.store.claim("k", HASH)
        self.assertIs(out.state, ClaimResult.REPLAY)
        self.assertEqual(out.result, {"bet_id": "b1"})

    def test_claim_returns_in_flight_when_concurrent(self):
        self.store.claim("k", HASH)  # claimed, not completed
        out = self.store.claim("k", HASH)
        self.assertIs(out.state, ClaimResult.IN_FLIGHT)
        self.assertIsNone(out.result)
        # ...and the result is NOT None, which is the whole point: a bare None
        # used to be ambiguous between this and a fresh claim.
        self.assertIsNotNone(out.state)

    def test_peek_reports_not_seen_then_in_flight_then_replay(self):
        self.assertIs(self.store.peek("k", HASH).state, ClaimResult.NOT_SEEN)
        self.store.claim("k", HASH)
        self.assertIs(self.store.peek("k", HASH).state, ClaimResult.IN_FLIGHT)
        self.store.complete("k", {"ok": 1})
        self.assertIs(self.store.peek("k", HASH).state, ClaimResult.REPLAY)

    def test_conflicting_payload_still_raises(self):
        self.store.claim("k", HASH)
        with self.assertRaises(IdempotencyConflict):
            self.store.claim("k", "other-hash")

    def test_failed_claim_releases_lock(self):
        self.assertIs(self.store.claim("k", HASH).state, ClaimResult.CLAIMED)
        self.assertIs(self.store.claim("k", HASH).state, ClaimResult.IN_FLIGHT)
        self.assertTrue(self.store.release("k", HASH))
        # Key is usable again immediately, not locked for the full TTL.
        self.assertIs(self.store.claim("k", HASH).state, ClaimResult.CLAIMED)

    def test_release_refuses_to_drop_a_completed_result(self):
        self.store.claim("k", HASH)
        self.store.complete("k", {"bet_id": "b1"})
        self.assertFalse(self.store.release("k", HASH))
        self.assertIs(self.store.claim("k", HASH).state, ClaimResult.REPLAY)
        self.assertEqual(self.store.claim("k", HASH).result, {"bet_id": "b1"})

    def test_release_refuses_a_mismatched_payload(self):
        self.store.claim("k", HASH)
        self.assertFalse(self.store.release("k", "other-hash"))
        self.assertIs(self.store.claim("k", HASH).state, ClaimResult.IN_FLIGHT)


class BetIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = replace(DEFAULT_CONFIG, confirmed=True)
        self.wallet = MemoryWallet()
        self.wallet.fund("p1", 10_000)
        self.svc = TeenPattiService(config=self.cfg, wallet=self.wallet)
        self.svc._now = lambda: 1_000_000
        self.svc.sessions.create("p1", "r1", "teen-patti-pro")
        self.svc._room("r1").create_session("p1")
        self.svc.start_round("r1")
        self.seat = self.cfg.seats[0]

    def test_duplicate_bet_request_does_not_double_debit(self):
        first = self.svc.place_bet("r1", "p1", self.seat, 100, "same-key")
        self.assertEqual(self.wallet.get_balance("p1").available, 9_900)
        # Sequential retry with the same key must replay, not re-debit.
        second = self.svc.place_bet("r1", "p1", self.seat, 100, "same-key")
        self.assertEqual(second, first)
        self.assertEqual(self.wallet.get_balance("p1").available, 9_900)
        self.assertEqual(len(self.svc._room("r1").round.bets), 1)

    def test_100_concurrent_same_key_debits_exactly_once(self):
        """100 threads, one Idempotency-Key: 1 bet, no double debit.

        Every loser must either replay the stored result or get a 409
        in-flight conflict. Executing the money path twice is the failure.
        """
        results, conflicts, unexpected = [], [], []
        barrier = threading.Barrier(100)
        lock = threading.Lock()

        def worker(i):
            barrier.wait()
            try:
                r = self.svc.place_bet("r1", "p1", self.seat, 100, "race-key")
                with lock:
                    results.append(r)
            except ServiceError as exc:
                with lock:
                    if exc.code == "STATE_CONFLICT":
                        conflicts.append(exc)
                    else:
                        unexpected.append(exc)
            except Exception as exc:  # noqa: BLE001 - surface anything else
                with lock:
                    unexpected.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(unexpected, [], f"unexpected errors: {unexpected[:3]}")
        self.assertEqual(len(self.svc._room("r1").round.bets), 1,
                         "exactly one bet must exist")
        self.assertEqual(self.wallet.get_balance("p1").available, 9_900,
                         "wallet must be debited exactly once")
        # Everyone who did not execute got a clean 409, not a duplicate.
        self.assertEqual(len(results) + len(conflicts), 100)
        if results:
            bet_ids = {r["bet_id"] for r in results}
            self.assertEqual(len(bet_ids), 1, "all replays must share one bet_id")

    def test_in_flight_key_rejected_with_409_and_retry_after(self):
        """A held key must block execution, not silently re-debit."""
        # Claim the exact key this bet will use, with the exact payload hash,
        # so the store reports IN_FLIGHT rather than a payload conflict.
        payload_hash = _h({"room": "r1", "player": "p1", "pos": self.seat, "amt": 100})
        self.assertIs(self.svc.idempotency.claim("bet:held", payload_hash).state,
                      ClaimResult.CLAIMED)
        with self.assertRaises(ServiceError) as ctx:
            self.svc.place_bet("r1", "p1", self.seat, 100, "held")
        self.assertEqual(ctx.exception.code, "STATE_CONFLICT")
        self.assertEqual(ctx.exception.retry_after, 1)
        self.assertEqual(self.wallet.get_balance("p1").available, 10_000,
                         "no money may move while the key is in flight")
        self.assertEqual(len(self.svc._room("r1").round.bets), 0)

    def test_debit_failure_releases_key_so_client_can_retry(self):
        class Boom(MemoryWallet):
            def __init__(self):
                super().__init__()
                self.fail = True

            def debit(self, player_id, amount, ref, idempotency_key):
                if self.fail:
                    raise RuntimeError("wallet down")
                return super().debit(player_id, amount, ref, idempotency_key)

        boom = Boom()
        boom.fund("p1", 10_000)
        svc = TeenPattiService(config=self.cfg, wallet=boom)
        svc._now = lambda: 1_000_000
        svc.sessions.create("p1", "r1", "teen-patti-pro")
        svc._room("r1").create_session("p1")
        svc.start_round("r1")

        with self.assertRaises(Exception):
            svc.place_bet("r1", "p1", self.cfg.seats[0], 100, "retry-me")
        # Key must NOT be stuck: after the outage clears the same key works.
        boom.fail = False
        out = svc.place_bet("r1", "p1", self.cfg.seats[0], 100, "retry-me")
        self.assertEqual(out["amount"], 100)
        self.assertEqual(boom.get_balance("p1").available, 9_900)


if __name__ == "__main__":
    unittest.main()
