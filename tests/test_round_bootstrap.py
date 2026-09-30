"""A seated table must always have a round, so the snapshot is never null.

Regression, seen live on the VPS: a freshly minted session subscribed over the
WebSocket and the server answered with a well-formed snapshot whose `round` was
`null`. The canvas had nothing to draw, so the page showed an empty table with
the status stuck on "connecting".

Why. In `h_create_session` the player was joined to the table -- which only
adds them to the room's `members` map -- but nothing called `ensure_round`.
`ensure_round` is the one thing that starts a round, and it deliberately refuses
to deal while `members` is empty. The two calls were split across different
code paths: join happened at session creation, ensure_round only happened on
the bet and choice routes, so a player who opened the game and waited sat at a
seated-but-empty table indefinitely.

This test drives the real service through a real session and asserts the round
exists, rather than asserting that a line of source calls a particular function.
"""
import unittest
from pathlib import Path

from games.teen_patti_pro.service import TeenPattiService


class _Wallet:
    def __init__(self):
        self.balance = 10_000_000
        self.writes = []

    def get_balance(self, player_id):
        class B:
            pass
        b = B()
        b.available = self.balance
        b.currency = "COIN"
        return b

    def debit(self, player_id, amount, **kw):
        self.balance -= amount
        self.writes.append(("debit", player_id, amount))
        return {"ok": True}

    def credit(self, player_id, amount, **kw):
        self.balance += amount
        self.writes.append(("credit", player_id, amount))
        return {"ok": True}

    def history(self, player_id, **kw):
        return []


class _Tokens:
    def mint(self, player, room, game):
        class T:
            pass
        t = T()
        t.token = "tok-" + player
        return t

    def redeem(self, token):
        return {"player_id": "player_1"}


class _Sessions:
    def __init__(self):
        self.s = {}

    def create(self, player_id, room_id, engine_id=None):
        class S:
            pass
        s = S()
        s.session_id = "sess-" + player_id
        s.player_id = player_id
        self.s[player_id] = s
        return s

    def touch(self, sid):
        pass

    def get(self, session_id):
        for s in self.s.values():
            if s.session_id == session_id:
                return s
        return None


class _Idem:
    def claim(self, key, payload_hash, ttl_s=86400):
        class C:
            ok = True
            conflict = False
        return C()

    def complete(self, key, result):
        pass

    def release(self, key, payload_hash=None):
        pass


def _service(confirmed: bool = False):
    """A service. `confirmed=True` lifts the TBC gate so settlement can run."""
    from games.teen_patti_pro.config import TeenPattiConfig
    kw = {}
    if confirmed:
        kw["config"] = TeenPattiConfig(confirmed=True)
    return TeenPattiService(wallet=_Wallet(), tokens=_Tokens(),
                           sessions=_Sessions(), idempotency=_Idem(), **kw)


class SubscribeBootstrapTest(unittest.TestCase):
    """The WebSocket subscribe path must seat the player *and* deal a round.

    Regression, seen live: a player opened the launch URL, the socket reported
    "live 252ms", the HUD said "Waiting for players -- No round is running" and
    the balance stayed null. The subscribe handler called
    `svc._room(room).create_session(player)` directly, which only adds the
    player to the room's members map. Round creation lives in the service
    (`ensure_round`, which refuses to deal into an empty room), and nothing on
    the socket path called it -- so the first connection sat at a table with no
    round. These tests drive the same call sequence the socket handler uses.
    """

    def test_claim_seat_then_ensure_round_gives_a_playable_snapshot(self):
        svc = _service()
        room = "teen-patti-low"
        svc.sessions.create("player_1", room, "teen-patti")
        # Exactly what the subscribe handler now does, in this order.
        out = svc.claim_seat(room, "player_1", "auto")
        self.assertEqual(out.get("status"), "seated")
        self.assertTrue(out.get("seat"), f"no seat assigned: {out}")
        # claim_seat now starts the round on first seat claim
        self.assertTrue(out.get("round_started"), f"claim_seat should start round: {out}")
        self.assertTrue(out.get("round_id"), f"no round_id from claim_seat: {out}")
        # ensure_round is now a no-op (round already active)
        started = svc.ensure_round(room)
        self.assertFalse(started.get("started"), f"ensure_round should be no-op: {started}")
        snap = svc.state(room, "player_1")
        self.assertTrue(snap.get("round_id"), f"null round in snapshot: {snap}")
        self.assertEqual(snap.get("status"), "BETTING_OPEN",
                         f"first seated player must land in a betting window: "
                         f"{snap.get('status')}")
        self.assertTrue(snap.get("betting_end_at"),
                        "no betting deadline -> timer cannot count down")

    def test_reconnect_does_not_restart_a_live_round(self):
        """A second connect must not kill the round the player is already in."""
        svc = _service()
        room = "teen-patti-low"
        svc.sessions.create("player_1", room, "teen-patti")
        out = svc.claim_seat(room, "player_1", "auto")
        self.assertTrue(out.get("round_started"))
        round_id = out["round_id"]

        again = svc.claim_seat(room, "player_1", "auto")
        self.assertEqual(again.get("seat"), "A", "reconnect lost its seat")
        self.assertFalse(again.get("round_started"),
                         f"reconnect should not restart round: {again}")
        second = svc.ensure_round(room)
        self.assertFalse(second.get("started"),
                         f"reconnect restarted the round: {second}")
        self.assertEqual(second.get("round_id"), round_id)

    def test_spectator_never_disturbs_the_table(self):
        """A full table yields spectator; their connect must not deal cards."""
        svc = _service()
        room = "teen-patti-low"
        for p in ("p1", "p2", "p3"):
            svc.sessions.create(p, room, "teen-patti")
            svc.claim_seat(room, p, "auto")
        svc.ensure_round(room)
        live = svc.state(room, "p1").get("round_id")

        svc.sessions.create("p4", room, "teen-patti")
        out = svc.claim_seat(room, "p4", "auto")
        self.assertEqual(out.get("status"), "spectator", f"4th player seated: {out}")
        self.assertFalse(svc.ensure_round(room).get("started"))
        self.assertEqual(svc.state(room, "p4").get("round_id"), live)
        self.assertTrue(svc.state(room, "p4").get("isSpectator"))


class SubscribeAuthTest(unittest.TestCase):
    """The launch token must authenticate the socket, whichever field it is in.

    Regression, seen live: the launch redirect appends `?session=gst_...` and the
    player page forwards that value in its subscribe frame's `session` field.
    `Hub.resolve_session` only redeemed tokens from `session_token`, so it fell
    through to the session store, found no session with that id, and answered
    "Unknown or expired session". The socket itself was open, so the HUD
    reported "live", but no seat was claimed, no snapshot was delivered and no
    round was ever started -- the field report was "Waiting for players" with a
    null balance on a table that looked connected.
    """

    class _Tok:
        def __init__(self):
            self.minted = {}

        def mint(self, session_id, claims, ttl_s):
            token = "gst_" + session_id.replace("sess-", "")
            self.minted[token] = session_id
            return {"token": token}

        def get(self, token):
            sid = self.minted.get(token)
            return {"session_id": sid} if sid else None

    def _hub(self):
        from games.teen_patti_pro.ws import Hub
        svc = _service()
        room = "teen-patti-low"
        svc.sessions.create("player_1", room, "teen-patti")
        hub = Hub(svc, tokens=self._Tok())
        token = hub.tokens.mint("sess-player_1", {}, 900)["token"]
        return hub, token, room

    def test_launch_token_in_session_field_authenticates(self):
        hub, token, _room = self._hub()
        sess, player = hub.resolve_session({"action": "subscribe",
                                            "session": token})
        self.assertIsNotNone(sess, "launch token rejected in the session field")
        self.assertEqual(player, "player_1")

    def test_launch_token_in_session_token_field_still_works(self):
        hub, token, _room = self._hub()
        sess, player = hub.resolve_session({"action": "subscribe",
                                            "session_token": token})
        self.assertIsNotNone(sess)
        self.assertEqual(player, "player_1")

    def test_plain_session_id_still_authenticates(self):
        hub, _token, _room = self._hub()
        sess, player = hub.resolve_session({"action": "subscribe",
                                            "session": "sess-player_1"})
        self.assertIsNotNone(sess, "plain session id stopped working")
        self.assertEqual(player, "player_1")

    def test_unknown_and_garbage_are_still_refused(self):
        hub, _token, _room = self._hub()
        for payload in ({}, {"session": ""}, {"session": "gst_nope"},
                        {"session": "../../etc/passwd"},
                        {"session": "gst_"}, {"session_token": None}):
            sess, player = hub.resolve_session(payload)
            self.assertIsNone(sess, f"accepted junk payload {payload}")
            self.assertEqual(player, "")


class RoundBootstrapTest(unittest.TestCase):
    def test_seated_table_has_a_round(self):
        svc = _service()
        room = "teen-patti-high"
        svc.sessions.create("player_1", room, "teen-patti")
        svc._room(room).create_session("player_1")   # what binding.join() does

        self.assertTrue(svc._room(room).members, "player should be seated")
        r = svc.ensure_round(room)
        self.assertTrue(r.get("round_id"), f"ensure_round dealt nothing: {r}")

    def test_snapshot_never_carries_a_null_round(self):
        svc = _service()
        room = "teen-patti-high"
        svc.sessions.create("player_1", room, "teen-patti")
        svc._room(room).create_session("player_1")
        svc.ensure_round(room)

        # state() returns a flat snapshot, not a nested "round" object: the
        # canvas reads round_id / status / hands / seats straight off it.
        snap = svc.state(room, "player_1")
        self.assertTrue(snap.get("round_id"),
                        f"snapshot has no round_id -> canvas draws nothing: {snap}")
        self.assertNotEqual(snap.get("status"), "WAITING")
        self.assertEqual(len(snap.get("hands") or {}), 3,
                         "all three seats must be dealt")
        for seat, hand in (snap.get("hands") or {}).items():
            self.assertEqual(len(hand), 3, f"seat {seat} has {len(hand)} cards")

    def test_ensure_round_is_idempotent(self):
        """Re-running must not deal a second round over a live one."""
        svc = _service()
        room = "teen-patti-high"
        svc.sessions.create("player_1", room, "teen-patti")
        svc._room(room).create_session("player_1")
        first = svc.ensure_round(room)
        second = svc.ensure_round(room)
        self.assertEqual(first["round_id"], second["round_id"])
        self.assertFalse(second.get("started"),
                         "a live round must not be restarted")

    def test_empty_room_still_refuses_to_deal(self):
        """Guard the other direction: this must not become 'always deal'."""
        svc = _service()
        r = svc.ensure_round("empty-room")
        self.assertEqual(r.get("status"), "WAITING")
        self.assertEqual(r.get("round_id"), "")


if __name__ == "__main__":
    unittest.main()


class IdleSnapshotShapeTest(unittest.TestCase):
    """The snapshot must have ONE shape, round or no round.

    `Room.snapshot` returned a three-key dict when no round existed
    (`room_id`, `round`, `serverTime`) and a full dict otherwise. Consumers
    reading `pot_total` / `my_bet` therefore got `undefined` on an idle table,
    and the game drew "Total Bet undefined  My Total Bet undefined" -- which is
    the state a player sees between rounds, so it was not a rare edge case.

    These tests pin one shape. The zero values are not placeholders: an idle
    table has a genuinely zero pot and zero personal stake, and `status` is
    explicitly WAITING.
    """

    def _idle(self):
        svc = _service()
        svc.sessions.create("player_1", "t", "teen-patti")
        return svc.state("t", "player_1")

    def test_idle_snapshot_has_the_same_keys_as_a_live_one(self):
        svc = _service()
        room = "t"
        svc.sessions.create("player_1", room, "teen-patti")
        idle = svc.state(room, "player_1")
        self.assertIsNone(idle["round"], "precondition: no round yet")

        svc._room(room).create_session("player_1")
        svc.ensure_round(room)
        live = svc.state(room, "player_1")
        self.assertIsNotNone(live["round_id"], "precondition: a round is dealt")

        for key in ("pot_total", "my_bet", "status", "pots", "seats", "hands"):
            self.assertIn(key, idle, f"idle snapshot is missing {key!r}")
            self.assertIn(key, live, f"live snapshot is missing {key!r}")

    def test_idle_pot_values_are_real_zeros_not_none(self):
        idle = self._idle()
        self.assertEqual(idle["pot_total"], 0)
        self.assertEqual(idle["my_bet"], 0)
        self.assertEqual(idle["status"], "WAITING")

    def test_never_none_for_numeric_fields(self):
        for snap in (self._idle(),):
            for key in ("pot_total", "my_bet", "carry_in", "round_no"):
                self.assertIsInstance(snap[key], (int, float),
                                      f"{key} must be numeric, got {snap[key]!r}")
                self.assertFalse(snap[key] is None)

    def test_idle_branch_carries_occupancy_fields(self):
        svc = _service()
        svc._room("t").claim_seat("p1")
        svc._room("t").claim_seat("p2")
        idle = svc.state("t", "ghost")
        self.assertIsNone(idle["round"], "precondition: no round yet")
        self.assertEqual(idle["seatOccupancy"],
                         {"A": "p1", "B": "p2", "C": None})
        self.assertEqual(idle["availableSeats"], ["C"])
        self.assertEqual(
            [(m["playerId"], m["seat"], m["status"]) for m in idle["members"]],
            [("p1", "A", "seated"), ("p2", "B", "seated")])
        self.assertTrue(idle["isSpectator"])
        self.assertIsNone(idle["mySeat"])


class RoundLifecyclePumpTest(unittest.TestCase):
    """A seated table must advance past BETTING_OPEN on its own.

    There was no driver at all. `start_round` ran when a player sat, but
    nothing ever called close_betting / publish_result / settle or dealt the
    next round, so a table dealt once and then sat in BETTING_OPEN against a
    `betting_end_at` that had already passed. Production showed "Betting closed
    -- waiting for the result" with no round actually running, and no bet could
    ever be placed because validate_bet rejects a closed window.

    `pump()` is the clock-driven transition, called once a second by the WebSocket
    hub's tick loop. It must be idempotent: pumping the same phase twice does
    nothing the second time.
    """

    def _seated(self, room="t", player="player_1", confirmed=True):
        svc = _service(confirmed=confirmed)
        svc.sessions.create(player, room, "teen-patti")
        svc._room(room).create_session(player)
        svc.ensure_round(room)
        return svc

    def test_pump_advances_a_round_past_betting_open(self):
        svc = self._seated()
        room = svc._room("t")
        r = room.round
        self.assertIsNotNone(r)
        # Blow the betting deadline, as the wall clock would.
        r.betting_end_at_ms = svc._now() - 1000

        moved = []
        for _ in range(12):                     # allow the chain to run
            out = svc.pump("t")
            moved += out.get("moved", [])
            if not out.get("moved"):
                break
        self.assertIn("betting.closed", moved,
                      f"betting never closed; moved={moved}")
        self.assertTrue({"result.declared", "settlement.completed",
                         "round.created"} & set(moved),
                      f"lifecycle did not progress past betting; moved={moved}")

    def test_pump_is_idempotent_within_a_phase(self):
        svc = self._seated()
        first = svc.pump("t")
        self.assertEqual(first["moved"], [],
                         "a live round with time left owes nothing")
        second = svc.pump("t")
        self.assertEqual(second["moved"], [])

    def test_pump_on_an_empty_room_is_a_no_op(self):
        svc = _service()
        out = svc.pump("nobody")
        self.assertEqual(out["moved"], [])
        self.assertIsNone(svc._room("nobody").round,
                          "an abandoned table must not deal cards to nobody")

    def test_round_id_changes_across_rounds(self):
        svc = self._seated()
        first_id = svc._room("t").round.round_id
        svc._room("t").round.betting_end_at_ms = svc._now() - 1000
        seen = {first_id}
        for _ in range(12):
            out = svc.pump("t")
            r = svc._room("t").round
            if r:
                seen.add(r.round_id)
            if "round.created" in out.get("moved", []):
                break
        self.assertGreater(len(seen), 1,
                           f"a new round was never created; ids seen: {seen}")
        fresh = {i for i in seen if i != first_id}
        self.assertEqual(fresh, seen - {first_id})
        self.assertTrue(fresh, "the new round must carry a new id, never the old one")


    def test_tbc_gate_defers_settlement_rather_than_breaking_the_table(self):
        """Unconfirmed rules must block payout, not crash the sweeper.

        The pump runs on a background thread. settle() raises when the rules
        are still TBC, and an exception escaping into tick_loop would kill the
        only clock-driven transition in the game -- so the failure is logged and
        the pump returns cleanly.
        """
        svc = self._seated(confirmed=False)
        svc._room("t").round.betting_end_at_ms = svc._now() - 1000
        out = svc.pump("t")            # must not raise
        self.assertIn("betting.closed", out["moved"])
        r = svc._room("t").round
        self.assertIsNotNone(r)
        self.assertEqual(r.status.value, "RESULT",
                         "result is published; settlement is what the TBC gate blocks")
        self.assertNotIn("settlement.completed", out["moved"])


class ClockLoggingTest(unittest.TestCase):
    """The lifecycle clock must be observable, and must not die on a typo.

    This exists because it already happened. `tick_loop` was instrumented with
    `log.info` / `log.exception`, but the module-level `log = logging.getLogger`
    never landed in ws.py -- the anchor string I used lives in service.py. The
    first call raised NameError *outside* the try block, which killed the daemon
    thread with no output. Production then sat on BETTING_OPEN with an expired
    deadline, and `except Exception: pass` around the pump had already hidden
    the earlier symptom.
    """

    def test_ws_module_defines_its_logger(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/ws.py").read_text(encoding="utf-8")
        self.assertIn("log = logging.getLogger(__name__)", src,
                      "ws.py uses log.* but never defines `log`")
        self.assertIn("import logging", src)

    def test_logger_is_defined_before_first_use(self):
        path = (Path(__file__).resolve().parents[1]
                / "games/teen_patti_pro/ws.py")
        lines = path.read_text(encoding="utf-8").split("\n")
        defined = next((i for i, l in enumerate(lines)
                        if l.startswith("log = logging.getLogger")), None)
        self.assertIsNotNone(defined, "no module-level logger")
        first_use = next((i for i, l in enumerate(lines)
                          if "log.info(" in l or "log.exception(" in l), None)
        self.assertIsNotNone(first_use, "the clock logs nothing at all")
        self.assertLess(defined, first_use,
                        "`log` is used before it is defined -- the daemon "
                        "thread would die on the first tick, silently")

    def test_tick_loop_has_no_silent_exception(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/ws.py").read_text(encoding="utf-8")
        i = src.index("def tick_loop(self):")
        body = src[i:src.index("def handle(", i)]
        self.assertNotIn("except Exception:\n            pass", body,
                         "tick_loop still swallows a failure silently")
        self.assertIn("log.exception(", body,
                      "a failure in the round clock must be logged")


class SessionRoomAuthorityTest(unittest.TestCase):
    """The room comes from the session, not from ?room=.

    Found by driving the real browser flow with Playwright. The current-round
    route read the room from the query string and defaulted it to "default",
    while a real session is issued against a concrete table (teen-patti-high).
    The client polls that route on a timer and assigns the result to S.snap, so
    the empty payload for the wrong room overwrote the authoritative snapshot the
    WebSocket had just sent. status stopped being BETTING_OPEN, placeBet()
    refused, and no bet request was ever made -- the table sat on "Betting
    closed" forever with a perfectly healthy round behind it.
    """

    def test_current_round_route_does_not_trust_the_query_room(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        i = src.index('r"/api/v1/games/teen-patti-pro/rounds/current"')
        block = src[i:i + 700]
        self.assertNotIn('qs.get("room"', block,
                         "current-round must resolve the room from the session")
        self.assertIn("self.session_room()", block)

    def test_result_route_does_not_trust_the_query_room(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        i = src.index('r"/api/v1/games/teen-patti-pro/rounds/(\\S+)/result"') \
            if 'rounds/(\\S+)/result' in src else \
            src.index('/result", path')
        block = src[i:i + 700]
        self.assertIn("self.session_room()", block)
        self.assertNotIn('qs.get("room", ["default"])[0]\n                  st = self.svc.state',
                         block)

    def test_session_room_helper_exists_and_prefers_the_session(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        self.assertIn("def session_room(self", src)
        i = src.index("def session_room(self")
        body = src[i:i + 1400]
        # session lookup must come before the query-string fallback
        self.assertLess(body.index("self.svc.sessions.get"),
                        body.index('qs.get("room")'),
                        "the session must be consulted before ?room=")

    def test_bets_route_binds_the_room_to_the_session(self):
        """A client must not nominate the table it bets on.

        The client builds the bets URL from its own ?room=, and play-url.sh
        hands out room=default, so the bet was validated against a room with no
        round and no betting window: 409 BETTING_CLOSED while a healthy round
        was open at the table the player was actually seated at.
        """
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        i = src.index('rooms/([^/]+)/(?:rounds/([^/]+)/)?bets')
        block = src[i:i + 1200]
        self.assertIn("room_id = self.session_room(fallback=m.group(1))", block,
                      "the bets route must resolve the room from the session")
        self.assertNotIn("room_id = m.group(1)\n", block,
                         "the bets route still trusts the path room")


class SeatOccupancyTest(unittest.TestCase):
    """Authoritative seats live in room.members, never in bets.

    The snapshot's `seats` key means bets-by-position and the betting renderer
    depends on that meaning, so occupancy is exposed separately as
    seatOccupancy/members/mySeat/isSpectator/availableSeats, in BOTH snapshot
    branches. No wallet, no bet, and no round is required to know who sits
    where.
    """

    def _room(self, svc, room="t"):
        return svc._room(room)

    def test_first_three_callers_take_abc_fourth_is_spectator(self):
        svc = _service()
        got = [self._room(svc).claim_seat(f"p{i}")["seat"] for i in range(1, 5)]
        self.assertEqual(got, ["A", "B", "C", None])
        snap = svc.state("t", "p4")
        self.assertTrue(snap["isSpectator"])
        self.assertIsNone(snap["mySeat"])
        self.assertEqual(snap["availableSeats"], [])

    def test_occupancy_visible_before_any_bet_or_round(self):
        svc = _service()
        svc._room("t").claim_seat("p1")
        snap = svc.state("t", "p1")
        self.assertEqual(snap["seatOccupancy"], {"A": "p1", "B": None, "C": None})
        self.assertEqual(snap["mySeat"], "A")
        self.assertFalse(snap["isSpectator"])
        self.assertEqual(snap["availableSeats"], ["B", "C"])
        self.assertEqual(
            [(m["playerId"], m["seat"], m["status"]) for m in snap["members"]],
            [("p1", "A", "seated")])

    def test_idle_branch_carries_occupancy_too(self):
        svc = _service()
        svc._room("t").claim_seat("p1")
        svc._room("t").claim_seat("p2")
        snap = svc.state("t", "ghost")
        self.assertIsNone(snap["round"])
        self.assertEqual(snap["seatOccupancy"]["A"], "p1")
        self.assertEqual(snap["seatOccupancy"]["B"], "p2")
        self.assertEqual(snap["seatOccupancy"]["C"], None)
        self.assertTrue(snap["isSpectator"])
        self.assertEqual(snap["availableSeats"], ["C"])
        self.assertEqual(len(snap["members"]), 2)

    def test_bets_by_position_meaning_is_untouched(self):
        svc = _service()
        svc._room("t").claim_seat("p1")
        snap = svc.state("t", "p1")
        self.assertEqual(snap["seats"], {},
                         "no bets yet: bets-by-position must stay empty")

    def test_explicit_taken_seat_conflicts(self):
        svc = _service()
        svc._room("t").claim_seat("p1", "B")
        from games.teen_patti_pro.engine import LifecycleError
        with self.assertRaises(LifecycleError):
            svc._room("t").claim_seat("p2", "B")
        # and through the service boundary it becomes a 409-class error
        from games.teen_patti_pro.service import ServiceError
        with self.assertRaises(ServiceError) as cm:
            svc.claim_seat("t", "p2", "B")
        self.assertEqual(cm.exception.code, "STATE_CONFLICT")

    def test_duplicate_claim_is_idempotent(self):
        svc = _service()
        first = svc._room("t").claim_seat("p1", "C")
        second = svc._room("t").claim_seat("p1", "A")
        self.assertEqual(first["seat"], "C")
        self.assertEqual(second["seat"], "C")
        self.assertFalse(second["claimed"])

    def test_reconnect_preserves_the_seat(self):
        svc = _service()
        svc._room("t").claim_seat("p1", "B")
        again = svc._room("t").create_session("p1")
        self.assertEqual(again["seat"], "B")
        self.assertEqual(again["status"], "seated")

    def test_concurrent_claims_yield_exactly_three_owners(self):
        import threading
        svc = _service()
        results, errors = {}, []
        barrier = threading.Barrier(6)

        def go(i):
            try:
                barrier.wait(timeout=10)
                results[f"c{i}"] = svc._room("t").claim_seat(f"c{i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        seated = sorted(r["seat"] for r in results.values() if r["seat"])
        self.assertEqual(seated, ["A", "B", "C"])
        self.assertEqual(sum(1 for r in results.values() if not r["seat"]), 3)
        owners = [svc._room("t").members[f"c{i}"].get("seat") for i in range(6)]
        self.assertEqual(sorted(o for o in owners if o), ["A", "B", "C"])


class SeatRouteBindingTest(unittest.TestCase):
    """POST .../rooms/:room/seat resolves the room from the session."""

    def test_route_exists_and_binds_session_room(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        i = src.index('rooms/([^/]+)/seat')
        block = src[i:i + 1500]
        self.assertIn("self.session_room(fallback=m.group(1))", block,
                      "seat route must resolve the room from the session")
        self.assertIn("self.svc.claim_seat(room_id, pid, want)", block)
        self.assertIn('"seat must be auto, A, B or C"', block)


class SnapshotRoundMarkerTest(unittest.TestCase):
    """A live round must be detectable from the snapshot alone.

    Regression, seen live on the VPS: the table was healthy -- round_id set,
    status BETTING_OPEN, advancing r18 -> r19 on its own -- and the page still
    showed "Waiting for players / No round is running at this table yet".

    Why. `Room.snapshot()` only emits a `round` key in its *idle* branch; the
    live branch omits it entirely. The client's `setVeilForState` gated on
    `!snap.round`, which is therefore true for every playing table, so the veil
    covered a working game. `round_id` is the authoritative marker, and the
    client now gates on that. This asserts the server half of that contract:
    a live snapshot carries a non-empty round_id, and the client does not gate
    on the absent `round` key.
    """

    def _service(self):
        svc = _service(confirmed=True)
        svc.sessions.create("p1", "t", "teen-patti-pro")
        svc.claim_seat("t", "p1", "auto")
        svc.ensure_round("t")
        return svc

    def test_live_snapshot_carries_a_round_id(self):
        snap = self._service().state("t", "p1")
        self.assertTrue(snap.get("round_id"),
                        "a live round must be identifiable by round_id")
        self.assertNotEqual(snap.get("status"), "WAITING")

    def test_idle_snapshot_is_distinguishable_from_live(self):
        svc = _service(confirmed=True)
        svc.sessions.create("p2", "t2", "teen-patti-pro")
        idle = svc.state("t2", "p2")
        self.assertFalse(idle.get("round_id"),
                         "an idle table must report no round_id")
        self.assertEqual(idle.get("status"), "WAITING")

    def _veil_expr(self):
        """The hasRound expression the client actually evaluates."""
        import re
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/client/game.js").read_text(encoding="utf-8")
        i = src.index("function setVeilForState")
        m = re.search(r"const hasRound = (.+);", src[i:i + 900])
        self.assertIsNotNone(m, "setVeilForState must compute a hasRound flag")
        return m.group(1)

    def _veil_says_busy(self, snap):
        """Run the client's own expression against a real snapshot, via node.

        Evaluated rather than pattern-matched. A substring test passed while the
        buggy `snap.round` was still in place, because the fixed expression also
        contains the word "round". The only assertion worth having is the one
        that runs the shipped expression against the shipped snapshot shape.
        """
        import json
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available to evaluate the client expression")
        expr = self._veil_expr()
        script = ("const snap = JSON.parse(process.argv[1]);"
                  "const hasRound = %s;"
                  "process.stdout.write(hasRound ? 'busy' : 'idle');" % expr)
        out = subprocess.run([node, "-e", script, json.dumps(snap)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0,
                         "client expression failed to evaluate: " + out.stderr)
        return out.stdout.strip() == "busy"

    def test_live_snapshot_clears_the_waiting_veil(self):
        snap = self._service().state("t", "p1")
        self.assertNotIn("round", snap,
                         "the live snapshot omits `round` entirely; that is the "
                         "field the old client gated on")
        self.assertTrue(self._veil_says_busy(snap),
                        "a live BETTING_OPEN snapshot must clear the veil")

    def test_idle_snapshot_still_shows_the_veil(self):
        svc = _service(confirmed=True)
        svc.sessions.create("p2", "t2", "teen-patti-pro")
        self.assertFalse(self._veil_says_busy(svc.state("t2", "p2")),
                         "an idle table must still show the waiting veil")


class RulesTypeGuardTest(unittest.TestCase):
    """A rule value of the wrong type must not reach the live config.

    Regression, seen live: an operator stored {"seats": 3} through the rules
    API. `_load_latest_rules` applied it verbatim, so cfg.seats became the int
    3. Every subsequent list(cfg.seats) raised TypeError, and /admin/games
    returned a bare 500 with a healthy database and a healthy service. The
    rules API accepts arbitrary JSON, so this is reachable by a normal panel
    edit -- the guard belongs where rules are applied.
    """

    def _guard(self):
        from games.teen_patti_pro.api import _coerce_config_kwargs
        from games.teen_patti_pro.config import TeenPattiConfig
        return _coerce_config_kwargs, TeenPattiConfig

    def test_int_for_a_tuple_field_is_dropped(self):
        guard, cfg_cls = self._guard()
        kw = {"seats": 3, "min_bet": 20}
        dropped = guard(cfg_cls, kw)
        self.assertTrue(any("seats" in d for d in dropped), dropped)
        self.assertNotIn("seats", kw, "the bad field must not survive")
        self.assertEqual(kw["min_bet"], 20, "valid fields still apply")
        # And the config must still be constructible from what remains.
        cfg_cls(**kw)

    def test_correct_types_are_kept(self):
        guard, cfg_cls = self._guard()
        kw = {"seats": ["A", "B", "C"], "min_bet": 20, "guess_ms": 30000,
              "version": "v1", "confirmed": True}
        dropped = guard(cfg_cls, kw)
        self.assertEqual(dropped, [])
        self.assertEqual(kw["seats"], ["A", "B", "C"])
        cfg_cls(**kw)

    def test_string_for_an_int_field_is_dropped(self):
        guard, cfg_cls = self._guard()
        kw = {"min_bet": "twenty"}
        dropped = guard(cfg_cls, kw)
        self.assertTrue(any("min_bet" in d for d in dropped), dropped)
        self.assertNotIn("min_bet", kw)

    def test_a_bool_is_not_accepted_for_an_int_field(self):
        # bool is a subclass of int, so True would silently become min_bet=1.
        guard, cfg_cls = self._guard()
        kw = {"min_bet": True}
        self.assertTrue(guard(cfg_cls, kw))
        self.assertNotIn("min_bet", kw)
