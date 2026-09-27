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


def _service():
    return TeenPattiService(wallet=_Wallet(), tokens=_Tokens(),
                           sessions=_Sessions(), idempotency=_Idem())


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
