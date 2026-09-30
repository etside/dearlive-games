"""A disconnected player's seat must come back, eventually.

The fault
---------
`Hub.leave()` removed the socket from the room's connection set and nothing
else. `svc.leave_table` was never called, so every abandoned tab left a ghost
member holding a seat for the lifetime of the process.

Why that mattered beyond a leak: the demo bot fill stands down when it counts
two or more players, so a table that had been opened and refreshed a few times
counted two "players" who were both ghosts. The fill never ran, and the ghosts
were shown to real players as seated opponents who never acted. A demo table
that has been reloaded twice is permanently un-fillable.

The fix delays the release so a reconnecting phone keeps its seat, and cancels
the release when the player comes back.
"""
import threading
import time
import unittest

from games.teen_patti_pro import ws as ws_mod
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService
from games.teen_patti_pro.ws import Hub

from tests.test_demo_bots import _service


class _Conn:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


class SeatReleaseTest(unittest.TestCase):
    def setUp(self):
        self.svc = _service()
        self.hub = Hub(self.svc, tokens=None)
        self.hub.bot_manager = None  # isolate the seat behaviour
        self.svc.sessions.create("p1", "room", "teen-patti-pro")
        self.svc.claim_seat("room", "p1", "auto")
        self.addCleanup(self._cancel_all)

    def _cancel_all(self):
        for timer in list(self.hub._pending_release.values()):
            timer.cancel()
        self.hub._pending_release.clear()

    def _release_now(self):
        for (room, pid) in list(self.hub._pending_release):
            self.hub._release_seat(room, pid)

    def test_disconnect_schedules_a_release_rather_than_dropping_the_seat(self):
        conn = _Conn("a")
        self.hub.join("room", conn, "p1")
        self.hub.leave(conn)
        # The seat is still held: a reconnect inside the grace window must
        # find it.
        self.assertIn("p1", self.svc._room("room").members)
        self.assertIn(("room", "p1"), self.hub._pending_release)

    def test_reconnect_cancels_the_release(self):
        conn = _Conn("a")
        self.hub.join("room", conn, "p1")
        self.hub.leave(conn)
        self.hub.join("room", _Conn("b"), "p1")
        self.assertNotIn(("room", "p1"), self.hub._pending_release)
        # Even when the timer would have fired, the seat survives.
        self._release_now()
        self.assertIn("p1", self.svc._room("room").members,
                      "a player who came back must keep their seat")

    def test_release_frees_the_seat_when_the_player_does_not_return(self):
        conn = _Conn("a")
        self.hub.join("room", conn, "p1")
        self.hub.leave(conn)
        self._release_now()
        self.assertNotIn("p1", self.svc._room("room").members,
                         "an abandoned seat must be given up, or the table "
                         "fills with ghosts and the bot fill stands down")
        self.assertFalse(self.svc._room("room")._occupancy("p1")["mySeat"])

    def test_a_second_tab_keeps_the_seat_alive(self):
        tab1, tab2 = _Conn("a"), _Conn("b")
        self.hub.join("room", tab1, "p1")
        self.hub.join("room", tab2, "p1")
        self.hub.leave(tab1)
        self.assertNotIn(("room", "p1"), self.hub._pending_release,
                         "one tab closing is not the player leaving")
        self._release_now()
        self.assertIn("p1", self.svc._room("room").members)

    def test_release_fires_without_a_manual_poke(self):
        # The timer is real: shorten it and wait, rather than calling the
        # release by hand, so a wiring mistake cannot pass this.
        original = ws_mod.SEAT_GRACE_S
        ws_mod.SEAT_GRACE_S = 0.05
        try:
            conn = _Conn("a")
            self.hub.join("room", conn, "p1")
            self.hub.leave(conn)
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if "p1" not in self.svc._room("room").members:
                    break
                time.sleep(0.02)
            self.assertNotIn("p1", self.svc._room("room").members,
                             "the release timer never fired")
        finally:
            ws_mod.SEAT_GRACE_S = original

    def test_release_notifies_the_bot_manager(self):
        calls = []

        class _BM:
            def on_player_leave(self, room, pid):
                calls.append((room, pid))

        self.hub.bot_manager = _BM()
        conn = _Conn("a")
        self.hub.join("room", conn, "p1")
        self.hub.leave(conn)
        self._release_now()
        self.assertEqual(calls, [("room", "p1")],
                         "a real player leaving must re-arm the bot fill")


class GhostTableTest(unittest.TestCase):
    """The failure this fixes, end to end: a reloaded demo table stays fillable."""

    def setUp(self):
        from games.teen_patti_pro import bot_manager as bm
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()
        bm.mark_demo_room("room")
        self.svc = _service()
        self.hub = Hub(self.svc, tokens=None)
        self.addCleanup(self._cancel_all)

    def _cancel_all(self):
        for t in list(self.hub._pending_release.values()):
            t.cancel()
        self.hub._pending_release.clear()

    def test_repeated_open_and_abandon_does_not_exhaust_the_table(self):
        for i in range(4):
            pid = "p%d" % i
            self.svc.sessions.create(pid, "room", "teen-patti-pro")
            conn = _Conn("c%d" % i)
            self.hub.join("room", conn, pid)
            self.svc.claim_seat("room", pid, "auto")
            self.hub.leave(conn)
            for (room, p) in list(self.hub._pending_release):
                self.hub._release_seat(room, p)
        occ = self.svc._room("room")._occupancy("p3")["seatOccupancy"]
        taken = [s for s, v in occ.items() if v]
        self.assertLessEqual(len(taken), 1,
                             "every abandoned seat was released, so %d of 3 "
                             "seats are still free" % (3 - len(taken)))


if __name__ == "__main__":
    unittest.main()
