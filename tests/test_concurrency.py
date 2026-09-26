"""Concurrency: close-vs-bet race, duplicate delivery, parallel bets."""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.lifecycle import LifecycleError
from common.wallet import MemoryWallet
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.engine import Room
from games.teen_patti_pro.service import TeenPattiService
from integrations.dearlive_mock import (MockDearLiveSessions, MockDearLiveTokens,
                                        MockDearLiveWallet)

CONF = TeenPattiConfig(confirmed=True, guess_ms=60_000)
T0 = 1_700_000_000_000


class TestCloseRace(unittest.TestCase):
    def test_bets_never_land_after_close(self):
        """Hammer bets while the window slams shut: every accepted bet must
        carry decision_time < betting_end_at (JEV late_bet_race fix)."""
        for trial in range(5):
            room = Room("race", CONF)
            r = room.start_round(T0, seed_hex=f"{trial:032x}")
            stop = threading.Event()
            accepted = []
            errors = []

            def bettor(i):
                k = 0
                while not stop.is_set():
                    try:
                        b = room.place_bet(f"p{i}", "ABC"[i % 3], 100,
                                           f"t{trial}-{i}-{k}", T0 + k)
                        accepted.append(b)
                    except LifecycleError:
                        pass
                    k += 1

            threads = [threading.Thread(target=bettor, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            room.close_betting(T0 + 50)  # slam shut almost immediately
            stop.set()
            for t in threads:
                t.join()
            for b in accepted:
                self.assertLess(b.decision_time_ms, r.betting_end_at_ms,
                                "bet accepted after close — race fix broken")

    def test_parallel_same_key_single_bet(self):
        room = Room("dup", CONF)
        room.start_round(T0, seed_hex="ff" * 16)
        results = []

        def go():
            try:
                results.append(room.place_bet("p1", "A", 100, "same-key", T0 + 1))
            except LifecycleError as e:
                results.append(e)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bets = [x for x in results if not isinstance(x, Exception)]
        self.assertEqual(len(bets), 8)
        self.assertEqual(len({b.bet_id for b in bets}), 1)  # one bet, replayed 8x
        self.assertEqual(len(room.round.bets), 1)


def _run10(fn):
    errors, results = [], []

    def worker():
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001 - collected, asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results, errors


class TestServiceIdempotencyx10(unittest.TestCase):
    """Deployability §7: same request x10 threads -> one bet/debit, one credit."""

    def test_same_bet_x10_one_debit_one_bet(self):
        w = MockDearLiveWallet()
        w.fund("p", 100000)
        svc = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=w)
        tok = svc.tokens.mint("p", "rc", "teen-patti-pro").token
        svc.open_session(tok)
        svc.start_round("rc", "test")
        results, errors = _run10(lambda: svc.place_bet("rc", "p", "A", 100, "conc-key-1"))
        self.assertEqual(len(errors), 0, f"errors: {errors[:2]}")
        self.assertEqual(len(results), 10)
        self.assertEqual(len({r["bet_id"] for r in results}), 1)
        self.assertEqual(w.get_balance("p").available, 100000 - 100)
        debits = [e for e in w.ledger if e["ref"].startswith("bet:rc:conc-key-1")]
        self.assertEqual(len(debits), 1)

    def test_same_settle_x10_one_credit(self):
        w = MockDearLiveWallet()
        w.fund("p", 100000)
        svc = TeenPattiService(config=TeenPattiConfig(confirmed=True), wallet=w)
        tok = svc.tokens.mint("p", "rs", "teen-patti-pro").token
        svc.open_session(tok)
        svc.start_round("rs", "test")
        svc.place_bet("rs", "p", "A", 100, "conc-key-2")
        svc.close_betting("rs")
        svc.publish_result("rs")
        before = w.get_balance("p").available
        results, errors = _run10(lambda: svc.settle("rs"))
        self.assertEqual(len(errors), 0, f"errors: {errors[:2]}")
        after = w.get_balance("p").available
        by_bet = {}
        for e in [x for x in w.ledger if x["ref"].startswith("settle:")]:
            by_bet[e["ref"]] = by_bet.get(e["ref"], 0) + 1
        self.assertTrue(all(n == 1 for n in by_bet.values()), f"credited once: {by_bet}")
        self.assertGreaterEqual(after, before)

