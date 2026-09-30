"""event_id and state_version: the contract the animation layer binds to.

Decision (a) of the rules lock. Without these the animation system has nothing
server-authoritative to key on: no way to make an effect idempotent across a
reconnect, and no way to tell whether a client missed a transition.

state_version is bumped by the `status` setter rather than by a counter at each
transition() call site, because there are status writes that do not go through
transition() -- notably the settlement-resume restore. A counter maintained by
hand would miss those and the animation layer would silently skip a transition.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from common.lifecycle import RoundStatus  # noqa: E402
from games.teen_patti_pro.engine import Room, Round  # noqa: E402


def _round():
    return Round(round_id="t-r1", round_no=1)


class StateVersionTest(unittest.TestCase):
    def test_starts_at_zero(self):
        self.assertEqual(_round().state_version, 0)

    def test_increments_on_a_real_change(self):
        r = _round()
        r.status = RoundStatus.BETTING_OPEN
        self.assertEqual(r.state_version, 1)

    def test_writing_the_same_status_does_not_bump(self):
        """Otherwise the counter stops meaning 'a transition happened'."""
        r = _round()
        r.status = RoundStatus.BETTING_OPEN
        v = r.state_version
        r.status = RoundStatus.BETTING_OPEN
        self.assertEqual(r.state_version, v)

    def test_tracks_a_whole_cycle_monotonically(self):
        r = _round()
        seen = [r.state_version]
        for st in (RoundStatus.BETTING_OPEN, RoundStatus.BETTING_CLOSED,
                   RoundStatus.RESULT_PROCESSING, RoundStatus.RESULT,
                   RoundStatus.SETTLED, RoundStatus.CLOSED):
            r.status = st
            seen.append(r.state_version)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), len(seen), "versions repeated: %s" % seen)

    def test_a_restore_bump_is_also_counted(self):
        """The settlement-resume path assigns status without transition()."""
        r = _round()
        r.status = RoundStatus.SETTLED_PENDING
        before = r.state_version
        r.status = RoundStatus.RESULT_PROCESSING   # resume
        self.assertGreater(r.state_version, before)


class EventIdTest(unittest.TestCase):
    def test_every_emit_carries_a_unique_event_id(self):
        r = _round()
        r.status = RoundStatus.BETTING_OPEN
        ids = {r.emit("e%d" % i, {}, 0)["event_id"] for i in range(50)}
        self.assertEqual(len(ids), 50, "event_id collided -- effects would replay")

    def test_event_id_is_a_usable_key(self):
        r = _round()
        ev = r.emit("x", {}, 0)
        self.assertIsInstance(ev["event_id"], str)
        self.assertGreaterEqual(len(ev["event_id"]), 16)

    def test_event_carries_the_state_version_at_emit_time(self):
        r = _round()
        r.status = RoundStatus.BETTING_OPEN
        first = r.emit("a", {}, 0)
        r.status = RoundStatus.BETTING_CLOSED
        second = r.emit("b", {}, 0)
        self.assertLess(first["state_version"], second["state_version"])
        self.assertEqual(first["state_version"], 1)
        self.assertEqual(second["state_version"], 2)

    def test_events_are_appended_in_order_with_seq(self):
        r = _round()
        seqs = [r.emit("e", {}, 0)["seq"] for _ in range(5)]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])


class SnapshotExposesStateVersionTest(unittest.TestCase):
    def test_idle_snapshot_reports_zero(self):
        snap = Room("t").snapshot("p1", now_ms=0)
        self.assertEqual(snap["state_version"], 0)

    def test_state_version_is_present_in_both_shapes(self):
        """state_version must not be one of the keys that come and go.

        The idle and live snapshots are NOT identical -- `round` appears only
        when idle and `raw_hands` only when live, both pre-existing. This
        asserts the key my change added is stable across both, which is what a
        client reading it after a reconnect actually depends on.
        """
        room = Room("t")
        self.assertIn("state_version", room.snapshot("p1", now_ms=0))
        room.start_round(1000)
        self.assertIn("state_version", room.snapshot("p1", now_ms=2000))

    def test_the_two_snapshot_shapes_keep_their_known_differences(self):
        """Recorded rather than asserted equal.

        `round` (idle only) and `raw_hands` (live only) predate this work.
        Silently equalising them would be a wire change with no test behind it,
        so they are pinned here to be noticed if either moves.
        """
        room = Room("t")
        idle = set(room.snapshot("p1", now_ms=0).keys())
        room.start_round(1000)
        live = set(room.snapshot("p1", now_ms=2000).keys())
        self.assertEqual(idle - live, {"round"})
        self.assertEqual(live - idle, {"raw_hands"})

    def test_live_snapshot_tracks_the_round(self):
        room = Room("t")
        room.start_round(1000)
        snap = room.snapshot("p1", now_ms=2000)
        self.assertEqual(snap["state_version"], room.round.state_version)
        self.assertGreaterEqual(snap["state_version"], 1)


class ClientConsumesTheContractTest(unittest.TestCase):
    """The client must be able to key animations on event_id."""

    def setUp(self):
        with open(os.path.join(ROOT, "games", "teen_patti_pro", "client", "game.js"),
                  encoding="utf-8") as fh:
            self.js = fh.read()

    def test_client_already_tracks_a_last_seq(self):
        self.assertIn("lastSeq", self.js,
                      "the client has a sequence cursor to extend, not replace")


if __name__ == "__main__":
    unittest.main()


class LoadingClipTest(unittest.TestCase):
    """The reference "blank to full" clip is a preloader, not a decoration.

    It covers the canvas until the first snapshot lands, and is removed then --
    on the websocket path AND on the polling fallback, because a table that
    fell back to polling has a snapshot too and would otherwise be stuck behind
    a preloader it can never satisfy.
    """
    def setUp(self):
        root = os.path.join(ROOT, "games", "teen_patti_pro", "client")
        with open(os.path.join(root, "index.html"), encoding="utf-8") as fh:
            self.html = fh.read()
        with open(os.path.join(root, "game.js"), encoding="utf-8") as fh:
            self.js = fh.read()

    def test_the_clip_ships_with_the_game(self):
        p = os.path.join(ROOT, "assets", "teen-patti", "loading-blank-to-full.mp4")
        self.assertTrue(os.path.isfile(p), "the loading clip is missing")
        self.assertGreater(os.path.getsize(p), 10_000, "the clip is truncated")

    def test_autoplay_is_possible(self):
        # iOS and most WebViews refuse autoplay unless muted + playsinline.
        # Without both, the preloader shows a black frame forever.
        self.assertIn("muted", self.html)
        self.assertIn("playsinline", self.html)
        self.assertIn("autoplay", self.html)

    def test_it_is_removed_not_just_faded(self):
        self.assertIn("removeChild(_boot)", self.js,
                      "a preloader left in the DOM can cover a playable table")
        self.assertIn("dismissBoot", self.js)

    def test_both_snapshot_paths_dismiss_it(self):
        # Exactly two call sites: the websocket snapshot and the polling
        # snapshot. Asserting three was wrong -- `function dismissBoot()` is a
        # declaration, not a call, and a third call site would mean a third
        # path had grown one.
        self.assertEqual(self.js.count("dismissBoot();"), 2,
                         "expected one call in the ws path and one in the "
                         "polling path")
        # And they must sit in the two different snapshot handlers.
        # Tolerant of the statements in between: the ws handler sets srvNow and
        # locNow between the assignment and the dismissal, so a [^;]* pattern
        # cannot span them.
        self.assertRegex(self.js, r"S\.snap = m\.data;[\s\S]{0,160}?dismissBoot\(\);",
                         "the websocket snapshot must dismiss the preloader")
        self.assertRegex(self.js, r"rounds/current[\s\S]{0,200}?dismissBoot\(\);",
                         "the polling snapshot must dismiss the preloader")

    def test_reduced_motion_replaces_the_clip(self):
        self.assertIn("prefers-reduced-motion", self.html,
                      "a looping clip must be replaced under reduced motion")

    def test_it_sits_above_the_canvas(self):
        import re
        m = re.search(r"\.tpp-boot\{[^}]*z-index:(\d+)", self.html)
        self.assertIsNotNone(m, "no preloader z-index")
        self.assertGreaterEqual(int(m.group(1)), 8,
                                "the preloader must sit above the canvas and the veil")


class ClientEventIdempotencyTest(unittest.TestCase):
    """An effect must be keyed on event_id, not on arrival."""

    def setUp(self):
        with open(os.path.join(ROOT, "games", "teen_patti_pro", "client", "game.js"),
                  encoding="utf-8") as fh:
            self.js = fh.read()

    def test_a_consumed_set_exists(self):
        self.assertIn("const consumedEvents = new Set();", self.js)

    def test_claim_precedes_animation(self):
        import re
        # Capture the whole branch. The first version of this stopped at
        # `const kind`, which is *before* the animate calls, so it searched a
        # body that contained no animation at all.
        m = re.search(r"else if \(m\.kind === 'event' && m\.data\) \{(.*?)\n          refresh\(\);",
                      self.js, re.S)
        self.assertIsNotNone(m, "the event branch was not found")
        body = m.group(1)
        self.assertIn("claimEvent", body, "the branch must claim the event")
        self.assertIn("animate", body, "the branch is expected to animate")
        self.assertLess(body.index("claimEvent"), body.index("animate"),
                        "the event must be claimed before anything animates")
        # ...and a claim failure must bail out rather than fall through.
        self.assertRegex(body, r"if \(!claimEvent\(m\.data\)\) \{[^}]*return;",
                         "an already-consumed event must not animate again")

    def test_the_set_is_bounded(self):
        self.assertIn("MAX_CONSUMED", self.js,
                      "an unbounded set is a slow memory leak in a long session")
