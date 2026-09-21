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


if __name__ == "__main__":
    unittest.main()
