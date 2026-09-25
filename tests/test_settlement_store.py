"""Settlement exactly-once tests (Step 2: UNIQUE(bet_id) at the store).

The guarantee used to be an in-process Python set, which cannot hold across two
workers or across a restart. It is now the store's: record() is an atomic
insert keyed on bet_id, so only one caller can ever own a payout.

These tests model two workers as two store objects over one shared backing
store -- the same relationship two processes have to one database.
"""
import threading
import unittest
from dataclasses import replace

from common.settlement import MemorySettlementStore
from common.wallet import MemoryWallet
from games.teen_patti_pro.config import DEFAULT_CONFIG
from games.teen_patti_pro.service import TeenPattiService

ROW = {"settlement_id": "stl-b1", "bet_id": "b1", "player_id": "p1",
       "payout": 100, "config_version": "v1"}


class StoreContractTest(unittest.TestCase):
    def test_duplicate_is_rejected_and_existing_row_returned(self):
        store = MemorySettlementStore()
        first, created = store.record(dict(ROW))
        self.assertTrue(created)
        second, created2 = store.record(dict(ROW))
        self.assertFalse(created2, "a duplicate bet_id must not create a second row")
        self.assertEqual(first["bet_id"], second["bet_id"])
        self.assertEqual(len(store.unpaid()), 1)

    def test_duplicate_with_differing_payout_keeps_the_first_row(self):
        """The first writer wins; a replay cannot inflate a payout."""
        store = MemorySettlementStore()
        store.record(dict(ROW))
        store.record({**ROW, "payout": 999_999})
        self.assertEqual(store.get("b1")["payout"], 100)

    def test_two_workers_one_payout(self):
        shared = {}
        workers = [MemorySettlementStore(shared) for _ in range(2)]
        results, lock = [], threading.Lock()
        barrier = threading.Barrier(len(workers))

        def run(store):
            barrier.wait()
            _row, created = store.record(dict(ROW))
            with lock:
                results.append(created)

        threads = [threading.Thread(target=run, args=(w,)) for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(sorted(results), [False, True],
                         "exactly one worker may own the payout")
        self.assertEqual(len(shared), 1, "exactly one row may exist")

    def test_recorded_but_uncredited_is_reported_as_unpaid(self):
        store = MemorySettlementStore()
        store.record(dict(ROW))
        self.assertEqual([r["bet_id"] for r in store.unpaid()], ["b1"])
        store.mark_credited("b1")
        self.assertEqual(store.unpaid(), [])

    def test_credited_flag_never_regresses(self):
        store = MemorySettlementStore()
        store.record(dict(ROW))
        store.mark_credited("b1")
        store.mark_credited("b1")
        self.assertTrue(store.get("b1")["credited"])


class ServiceSettlementTest(unittest.TestCase):
    def setUp(self):
        self.cfg = replace(DEFAULT_CONFIG, confirmed=True)
        self.opening = 10_000

    def _svc(self, wallet, store=None, now=1_000_000):
        svc = TeenPattiService(config=self.cfg, wallet=wallet,
                               settlement_store=store or MemorySettlementStore())
        svc._now = lambda: now
        return svc

    def _played_round(self, svc, player="p1", per_seat=100):
        """Bet every seat, then settle. A clean run returns the opening balance."""
        svc.sessions.create(player, "r1", "teen-patti-pro")
        svc._room("r1").create_session(player)
        svc.start_round("r1")
        for i, seat in enumerate(svc.config.seats):
            svc.place_bet("r1", player, seat, per_seat, f"k-{i}")
        svc.close_betting("r1")
        svc.publish_result("r1")
        return svc.settle("r1")

    def test_repeat_settle_pays_once(self):
        wallet = MemoryWallet()
        wallet.fund("p1", self.opening)
        svc = self._svc(wallet)
        self._played_round(svc)
        after_first = wallet.get_balance("p1").available
        self.assertEqual(after_first, self.opening, "winner is made whole")

        for _ in range(5):
            svc.settle("r1")
        self.assertEqual(wallet.get_balance("p1").available, after_first,
                         "repeat settle must not pay again")

    def test_two_workers_same_round_single_payout(self):
        """Two services, one shared store, one wallet: exactly one payout."""
        shared_store = {}
        wallet = MemoryWallet()
        wallet.fund("p1", self.opening)

        a = self._svc(wallet, MemorySettlementStore(shared_store))
        b = self._svc(wallet, MemorySettlementStore(shared_store))
        self._played_round(a)
        after_a = wallet.get_balance("p1").available

        # Worker B settles the same round with its own in-memory engine state
        # (as a second process would have after rehydrating from the store).
        b.rooms["r1"] = a.rooms["r1"]
        b.settle("r1")
        self.assertEqual(wallet.get_balance("p1").available, after_a,
                         "a second worker must not pay the same bet again")
        self.assertEqual(len(shared_store), 3, "one row per bet, no duplicates")

    def test_crash_between_record_and_credit_is_paid_on_retry(self):
        """recorded but not credited => still owed, and paid exactly once."""
        store = MemorySettlementStore()
        wallet = MemoryWallet()
        wallet.fund("p1", self.opening)
        svc = self._svc(wallet, store)
        self._played_round(svc)

        # The winning bet is whichever seat took the pot; find it rather than
        # assuming a bet_id, since seat A may have lost to B or C.
        winner = max(store._rows.values(), key=lambda r: r["payout"])
        self.assertGreater(winner["payout"], 0, "the test needs a real payout")

        # Simulate a crash after record() but before mark_credited().
        # A faithful crash means the credit left NO trace in the wallet, so the
        # wallet's idempotency record has to go too. Leaving it in place would
        # make the retry a silent replay -- which is precisely how a real
        # double-pay guard works, and worth asserting explicitly below.
        key = f"settle:{winner['bet_id']}"
        self.assertIn(key, wallet.txns, "the first settle did credit")
        del wallet.txns[key]
        winner["credited"] = False
        wallet.balances["p1"] -= winner["payout"]  # the money never arrived
        stranded = wallet.get_balance("p1").available
        self.assertLess(stranded, self.opening)

        svc.settle("r1")
        self.assertEqual(wallet.get_balance("p1").available, self.opening,
                         "the owed payout must be completed on retry")
        svc.settle("r1")
        self.assertEqual(wallet.get_balance("p1").available, self.opening,
                         "and still only once")
        self.assertEqual(store.get(winner["bet_id"])["credited"], True)

    def test_wallet_idempotency_is_the_second_guard(self):
        """Even with the store row present, a replayed credit key pays nothing.

        Documents that exactly-once rests on BOTH the store's UNIQUE(bet_id)
        and the wallet's idempotency key. Remove either one and a retry can
        double-pay.
        """
        wallet = MemoryWallet()
        wallet.fund("p1", self.opening)
        svc = self._svc(wallet)
        self._played_round(svc)
        settled = wallet.get_balance("p1").available

        winner = max(svc.settlements._rows.values(), key=lambda r: r["payout"])
        # Replay the same credit key the wallet already saw.
        replay = wallet.credit(winner["player_id"], winner["payout"],
                               ref=f"settle:{winner['bet_id']}",
                               idempotency_key=f"settle:{winner['bet_id']}")
        self.assertEqual(replay.txn_id.startswith("txn-"), True)
        self.assertEqual(wallet.get_balance("p1").available, settled,
                         "a replayed settlement credit must be a no-op")

    def test_service_no_longer_uses_an_in_process_set(self):
        wallet = MemoryWallet()
        wallet.fund("p1", self.opening)
        svc = self._svc(wallet)
        self.assertFalse(hasattr(svc, "settled_bet_ids"),
                         "the in-process set must be gone")
        self._played_round(svc)
        self.assertEqual(len(svc.settlements.unpaid()), 0,
                         "nothing left unpaid after a clean settle")


if __name__ == "__main__":
    unittest.main()
