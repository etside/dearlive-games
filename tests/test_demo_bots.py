"""Demo bots: one real player, no company, bots fill the table.

Behaviour under test
--------------------
* A room is bot-eligible only if the demo launch path marked it. Nothing about
  the process environment can make a table eligible.
* One real player arms a fill with a 40s deadline. A second real player inside
  that window cancels it. When the deadline passes with nobody else joined,
  bots take the free seats.
* A real player always wins a seat: a bot gives it up.
* Production refuses bots outright, whatever the room marking says.
* A bot is not distinguishable in a snapshot: no is_bot field reaches a player.

The old implementation failed most of these silently. It gated on DEMO_MODE=1
in the environment (global, so it would have filled real provider tables in the
same process), credited bots with debit(-10000) which the wallet adapter
rejects because it refuses a non-positive amount, and wrapped every spawn in
`except Exception: pass`. So no bot ever appeared and nothing said why. The
avatar list pointed at eight files that do not exist in the pack.
"""
import os
import time
import unittest

from games.teen_patti_pro import bot_manager as bm
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService


class _Wallet:
    """Mirrors DemoWalletAdapter: opens an account at a starting balance."""

    def __init__(self, starting=10_000):
        self.balances = {}
        self.starting = starting
        self.writes = []

    def _ensure(self, pid):
        return self.balances.setdefault(pid, self.starting)

    def get_balance(self, pid):
        class B:
            pass
        b = B()
        b.available = self._ensure(pid)
        b.currency = "COIN"
        return b

    def debit(self, pid, amount, ref, idempotency_key):
        if amount <= 0:
            raise ValueError("amount must be positive")
        if self._ensure(pid) < amount:
            raise ValueError("insufficient")
        self.balances[pid] -= amount
        self.writes.append(("debit", pid, amount))
        class R:
            pass
        r = R()
        r.txn_id = idempotency_key
        return r

    def credit(self, pid, amount, ref, idempotency_key):
        self.balances[pid] = self._ensure(pid) + amount
        self.writes.append(("credit", pid, amount))
        class R:
            pass
        r = R()
        r.txn_id = idempotency_key
        return r

    def void_debit(self, pid, ref, idempotency_key):
        class R:
            pass
        r = R()
        r.txn_id = idempotency_key
        return r

    def rollback(self, txn_id):
        return {"ok": True}


class _Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, action, entity, entity_id, before=None, after=None):
        self.entries.append((actor, action, entity, entity_id))
        return {"ok": True}


# Use the service's own idempotency store. A hand-rolled stub is a trap here:
# place_bet compares claim()'s `state` against the ClaimResult ENUM, so a stub
# returning the string "NOT_SEEN" is not IN_FLIGHT and not NOT_SEEN either, and
# every bet raises "key is in flight" before any money moves. The first version
# of this file did exactly that and looked like a bot-betting bug.
class _UnusedIdem:
    pass


def _service():
    svc = TeenPattiService(
        wallet=_Wallet(),
        config=TeenPattiConfig(confirmed=True))
    # The service builds its own AuditLog; swap in a recording one so the
    # tests can assert a bot taking a seat is auditable.
    svc.audit = _Audit()
    return svc


class DemoRoomMarkingTest(unittest.TestCase):
    """Only the demo launch path may make a room bot-eligible."""

    def setUp(self):
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()

    def tearDown(self):
        bm._DEMO_ROOMS.clear()
        bm._DEMO_ROOMS.update(self._saved)

    def test_unmarked_room_is_not_eligible(self):
        self.assertFalse(bm.is_demo_room("some-real-room"))

    def test_marking_is_what_makes_it_eligible(self):
        bm.mark_demo_room("demo-low")
        self.assertTrue(bm.is_demo_room("demo-low"))

    def test_no_environment_variable_can_mark_a_room(self):
        # The old gate was DEMO_MODE=1. Nothing in the environment may stand in
        # for the marking, or a staging host would bot real-money tables.
        for var in ("DEMO_MODE", "DEMO_BOTS", "DEMO_BOT_FILL_DELAY_S"):
            os.environ[var] = "1"
            try:
                self.assertFalse(bm.is_demo_room("unmarked-%s" % var))
            finally:
                os.environ.pop(var, None)

    def test_clear_removes_the_mark(self):
        bm.mark_demo_room("demo-low")
        bm.clear_demo_room("demo-low")
        self.assertFalse(bm.is_demo_room("demo-low"))


class FillArmingTest(unittest.TestCase):
    """The 40s window and what cancels it."""

    def setUp(self):
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()
        self.svc = _service()
        self.svc.sessions.create("human", "demo-low", "teen-patti-pro")
        # Mirror the real subscribe order in ws.py: the socket claims a seat
        # first, and only then tells the bot manager a human is here.
        self.svc.claim_seat("demo-low", "human", "auto")
        bm.mark_demo_room("demo-low")
        self.mgr = bm.BotManager(self.svc, fill_delay_s=40.0)
        self.addCleanup(self.mgr.stop)

    def tearDown(self):
        bm._DEMO_ROOMS.clear()
        bm._DEMO_ROOMS.update(self._saved)

    def test_one_real_player_arms_a_40_second_fill(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.assertIn("demo-low", self.mgr._armed)
        remaining = self.mgr._armed["demo-low"] - int(time.time() * 1000)
        self.assertGreater(remaining, 30_000,
                           "the operator asked for 40s before bots arrive")
        self.assertLessEqual(remaining, 40_500)

    def test_default_fill_delay_is_forty_seconds(self):
        self.assertEqual(bm.FILL_DELAY_S, 40.0)

    def test_a_second_real_player_cancels_the_fill(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.svc.sessions.create("human2", "demo-low", "teen-patti-pro")
        self.svc.claim_seat("demo-low", "human2", "auto")
        self.mgr.on_player_join("demo-low", "human2", is_real=True)
        self.assertNotIn("demo-low", self.mgr._armed,
                         "two real players must not get bots")

    def test_an_unmarked_room_is_never_armed(self):
        self.svc.sessions.create("human", "real-low", "teen-patti-pro")
        self.svc.claim_seat("real-low", "human", "auto")
        self.mgr.on_player_join("real-low", "human", is_real=True)
        self.assertNotIn("real-low", self.mgr._armed)

    def test_far_short_deadline_fills_the_table(self):
        # Drive the deadline into the past rather than sleeping 40s.
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = int(time.time() * 1000) - 1
        self.mgr._tick()
        seats = self.mgr._seated("demo-low")
        self.assertGreaterEqual(len(seats), 2,
                                "a lone player must get company at the table")
        self.assertIn("human", self.svc._room("demo-low").members)

    def test_bots_leave_the_human_alone_and_take_other_seats(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        room = self.svc._room("demo-low")
        self.assertTrue((room.members["human"] or {}).get("seat"))
        bot_seats = {(m or {}).get("seat") for pid, m in room.members.items()
                     if pid != "human"}
        human_seat = (room.members["human"] or {}).get("seat")
        self.assertNotIn(human_seat, bot_seats)

    def test_bots_are_audited(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        actions = [e[1] for e in self.svc.audit.entries]
        self.assertIn("bot.seated", actions,
                      "a bot taking a seat is an auditable act")


class OneBetPerRoundTest(unittest.TestCase):
    """A bot acts once per round, on purpose rather than by rejection.

    The armed-marker doubles as the acted-marker, so after a bot's bet landed
    it still looked due on the next tick and tried again. The per-round
    idempotency key kept the money correct, but a retry that picked a different
    chip has a different payload, so the service rejected it -- and the journal
    filled with "Idempotency key reused with different payload" once a second
    for the rest of the betting window.
    """

    def setUp(self):
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()
        self.svc = _service()
        self.svc.sessions.create("human", "demo-low", "teen-patti-pro")
        self.svc.claim_seat("demo-low", "human", "auto")
        bm.mark_demo_room("demo-low")
        self.mgr = bm.BotManager(self.svc, start=False)
        self.addCleanup(self.mgr.stop)

    def tearDown(self):
        bm._DEMO_ROOMS.clear()
        bm._DEMO_ROOMS.update(self._saved)

    def test_repeated_ticks_place_exactly_one_bet(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        bot = next(b for b in self.mgr._bots["demo-low"])
        room = self.svc._room("demo-low")
        now = int(time.time() * 1000)
        self.mgr._maybe_bet("demo-low", bot, now)
        self.mgr._maybe_bet("demo-low", bot, bot.bet_at_ms)
        placed = len([b for b in room.round.bets
                      if b.player_id == bot.player_id
                      and b.status == "accepted"])
        self.assertEqual(placed, 1)
        # Keep ticking for the rest of the window.
        for _ in range(20):
            self.mgr._maybe_bet("demo-low", bot, bot.bet_at_ms + 1)
        again = len([b for b in room.round.bets
                     if b.player_id == bot.player_id
                     and b.status == "accepted"])
        self.assertEqual(again, placed,
                         "a bot must not re-bet inside one round")
        self.assertEqual(bot.bet_done, room.round.round_id)

    def test_a_new_round_re_arms_the_bot(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        bot = next(b for b in self.mgr._bots["demo-low"])
        room = self.svc._room("demo-low")
        now = int(time.time() * 1000)
        self.mgr._maybe_bet("demo-low", bot, now)
        self.mgr._maybe_bet("demo-low", bot, bot.bet_at_ms)
        first = room.round.round_id
        # Drive the round forward the way the sweep does.
        self.svc.close_betting("demo-low")
        self.svc.publish_result("demo-low")
        self.svc.settle("demo-low")
        self.svc.ensure_round("demo-low")
        new_round = self.svc._room("demo-low").round
        self.assertNotEqual(new_round.round_id, first)
        self.mgr._maybe_bet("demo-low", bot, int(time.time() * 1000))
        self.assertEqual(bot.bet_round_id, new_round.round_id,
                         "the bot must arm again for the new round")


class ChipDenominationTest(unittest.TestCase):
    """A bot must only ever choose a chip the table accepts.

    Regression: _choose_chip returned 1000/10000/50000 while the shipped
    config's denominations are (20, 100, 500, 1000). Every bet above 1000 was
    rejected with "Amount not a configured denomination", logged at info and
    skipped -- so a demo table filled with bots that never played, which reads
    exactly like a broken game.
    """

    def setUp(self):
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()
        self.svc = _service()
        bm.mark_demo_room("demo-low")
        self.mgr = bm.BotManager(self.svc, start=False)
        self.addCleanup(self.mgr.stop)

    def tearDown(self):
        bm._DEMO_ROOMS.clear()
        bm._DEMO_ROOMS.update(self._saved)

    def test_every_chosen_chip_is_a_configured_denomination(self):
        denoms = set(int(d) for d in self.svc.config.denoms)
        for _ in range(400):
            self.assertIn(self.mgr._choose_chip(), denoms)

    def test_a_bot_bet_is_never_rejected_as_a_bad_denomination(self):
        self.svc.sessions.create("human", "demo-low", "teen-patti-pro")
        self.svc.claim_seat("demo-low", "human", "auto")
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        bot = next(b for b in self.mgr._bots["demo-low"])
        room = self.svc._room("demo-low")
        now = int(time.time() * 1000)
        # Drive the human delay out for every bot, over a few rounds' worth of
        # calls, and assert nothing was thrown away for the wrong reason.
        rejected = []
        for _ in range(6):
            for b in self.mgr._bots["demo-low"]:
                self.mgr._maybe_bet("demo-low", b, now)
                self.mgr._maybe_bet("demo-low", b, b.bet_at_ms)
        accepted = [b for b in room.round.bets
                    if b.player_id in self.mgr.bot_ids("demo-low")
                    and b.status == "accepted"]
        self.assertTrue(accepted, "the bot must land a bet")
        self.assertEqual(rejected, [])


class RealPlayerPriorityTest(unittest.TestCase):
    """A human always beats a bot for a seat."""

    def setUp(self):
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()
        self.svc = _service()
        self.svc.sessions.create("human", "demo-low", "teen-patti-pro")
        # Mirror the real subscribe order in ws.py: the socket claims a seat
        # first, and only then tells the bot manager a human is here.
        self.svc.claim_seat("demo-low", "human", "auto")
        bm.mark_demo_room("demo-low")
        # start=False: the reaper is driven explicitly below, so a round cannot
        # roll over between two assertions.
        self.mgr = bm.BotManager(self.svc, fill_delay_s=0.0, start=False)
        self.addCleanup(self.mgr.stop)

    def tearDown(self):
        bm._DEMO_ROOMS.clear()
        bm._DEMO_ROOMS.update(self._saved)

    def test_a_human_joining_takes_over_a_bot_seat(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        room = self.svc._room("demo-low")
        self.assertTrue((room.members.get("human") or {}).get("seat"))

        self.svc.sessions.create("human2", "demo-low", "teen-patti-pro")
        self.svc.claim_seat("demo-low", "human2", "auto")
        self.mgr.on_player_join("demo-low", "human2", is_real=True)
        room = self.svc._room("demo-low")
        self.assertTrue((room.members.get("human2") or {}).get("seat"),
                        "a real player must never be made a spectator "
                        "because a bot held the seat")
        self.assertLessEqual(len(self.mgr.bot_ids("demo-low")), 1)

    def test_bots_are_never_recorded_as_players_in_a_snapshot(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        snap = self.svc.state("demo-low", "human")
        blob = repr(snap)
        self.assertNotIn("is_bot", blob,
                         "a bot must be indistinguishable in a snapshot")
        for bot_id in self.mgr.bot_ids("demo-low"):
            # The id legitimately appears (members, seatOccupancy); what must
            # not appear is any flag naming it a bot.
            self.assertNotIn("bot_" + bot_id.split("bot_")[-1][:6] + ":true",
                             blob.replace(" ", ""))
        self.assertEqual(snap.get("isSpectator"), False)


class BotBettingTest(unittest.TestCase):
    """A seated bot actually plays."""

    def setUp(self):
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()
        self.svc = _service()
        self.svc.sessions.create("human", "demo-low", "teen-patti-pro")
        # Mirror the real subscribe order in ws.py: the socket claims a seat
        # first, and only then tells the bot manager a human is here.
        self.svc.claim_seat("demo-low", "human", "auto")
        bm.mark_demo_room("demo-low")
        # start=False: the reaper is driven explicitly below, so a round cannot
        # roll over between two assertions.
        self.mgr = bm.BotManager(self.svc, fill_delay_s=0.0, start=False)
        self.addCleanup(self.mgr.stop)

    def tearDown(self):
        bm._DEMO_ROOMS.clear()
        bm._DEMO_ROOMS.update(self._saved)

    def test_a_bot_places_a_bet_inside_the_betting_window(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        bot_ids = self.mgr.bot_ids("demo-low")
        self.assertTrue(bot_ids, "expected a bot to be seated")
        room = self.svc._room("demo-low")
        self.assertIsNotNone(room.round)
        bot = next(b for b in self.mgr._bots["demo-low"])

        # First look arms a human delay; force it due, then act.
        now = int(time.time() * 1000)
        self.mgr._maybe_bet("demo-low", bot, now)
        self.assertTrue(bot.bet_round_id, "the bot must arm for this round")
        # The arming must be a delay a human would keep, not a timestamp in
        # the far future. reset_for_round adds the duration to now_ms, so
        # passing an absolute time here stored 2*now_ms and the guard below
        # never expired -- the table filled with bots that never bet.
        self.assertGreater(bot.bet_at_ms, now, "the bot must be armed to bet")
        self.assertLess(bot.bet_at_ms - now, 15_000,
                        "the arming must be seconds away, not hours")
        # Let the delay elapse the way the reaper would.
        self.mgr._maybe_bet("demo-low", bot, bot.bet_at_ms)

        bets = [b for b in room.round.bets
                if b.player_id in bot_ids and b.status == "accepted"]
        self.assertTrue(bets, "a seated bot must bet during BETTING_OPEN")
        self.assertIn(bets[0].amount, (1000, 10000, 50000))

    def test_a_bot_does_not_bet_twice_in_one_round(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        bot = next(b for b in self.mgr._bots["demo-low"])
        now = int(time.time() * 1000)
        self.mgr._maybe_bet("demo-low", bot, now)
        self.mgr._maybe_bet("demo-low", bot, bot.bet_at_ms)
        room = self.svc._room("demo-low")
        before = len([b for b in room.round.bets
                      if b.player_id == bot.player_id and b.status == "accepted"])
        self.mgr._maybe_bet("demo-low", bot, now)
        after = len([b for b in room.round.bets
                     if b.player_id == bot.player_id and b.status == "accepted"])
        self.assertEqual(before, after, "one bet per round, per bot")

    def test_bot_bets_move_real_money_through_the_service(self):
        self.mgr.on_player_join("demo-low", "human", is_real=True)
        self.mgr._armed["demo-low"] = 0
        self.mgr._tick()
        bot = next(b for b in self.mgr._bots["demo-low"])
        now = int(time.time() * 1000)
        self.mgr._maybe_bet("demo-low", bot, now)
        self.mgr._maybe_bet("demo-low", bot, bot.bet_at_ms)
        room = self.svc._room("demo-low")
        bet = [b for b in room.round.bets
               if b.player_id == bot.player_id and b.status == "accepted"]
        self.assertTrue(bet)
        self.assertLess(self.svc.wallet.get_balance(bot.player_id).available,
                        10_000,
                        "the bot's stake must come out of its own balance")


class ProductionGuardTest(unittest.TestCase):
    """A misconfigured production host still gets no bots."""

    def setUp(self):
        self._saved = set(bm._DEMO_ROOMS)
        bm._DEMO_ROOMS.clear()
        self._env = os.environ.get("APP_ENV")

    def tearDown(self):
        bm._DEMO_ROOMS.clear()
        bm._DEMO_ROOMS.update(self._saved)
        if self._env is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = self._env

    def test_production_refuses_even_a_marked_room(self):
        os.environ["APP_ENV"] = "production"
        svc = _service()
        svc.sessions.create("human", "demo-low", "teen-patti-pro")
        svc.claim_seat("demo-low", "human", "auto")
        bm.mark_demo_room("demo-low")
        mgr = bm.BotManager(svc, fill_delay_s=0.0, start=False)
        self.addCleanup(mgr.stop)
        mgr.on_player_join("demo-low", "human", is_real=True)
        mgr._tick()
        self.assertEqual(mgr.bot_ids("demo-low"), set(),
                         "no bot may be seated in production")


class AvatarTest(unittest.TestCase):
    """The avatar art bots reference has to exist."""

    def test_avatar_and_frame_files_are_referenced_by_the_client(self):
        # The previous list was avatar-01..08, none of which are in the pack,
        # so every bot resolved to a broken image. Assert against the real set.
        for url in [bm.BOT_AVATAR] + bm.BOT_FRAMES:
            self.assertTrue(url.endswith(".svg"), url)
            self.assertIn("/assets/games/teen-patti-pro/avatars/", url)
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        for url in [bm.BOT_AVATAR] + bm.BOT_FRAMES:
            rel = url.lstrip("/")
            self.assertTrue((root / rel).is_file(),
                            "bot art does not exist: %s" % url)


if __name__ == "__main__":
    unittest.main()
