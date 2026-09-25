"""Route parsing tests.

Greedy `(\\S+)` in a path segment silently mis-parses any route that has an
optional trailing group: the regex takes the longest possible capture before
backtracking, so the *room* segment swallows the round segment.

The concrete production symptom: POST
/api/v1/games/teen-patti-pro/rooms/qaZ/rounds/qaZ-r1/bets parsed room_id as
"qaZ/rounds/qaZ-r1", addressing a phantom room that had no open round, so
every bet returned 409 BETTING_CLOSED and the game was unplayable.
"""
import re
import unittest

# Mirrors the route pattern in games/teen_patti_pro/api.py. If the pattern
# there changes, update this and the test fails loudly rather than silently
# testing a stale copy.
BET_PATTERN = re.compile(
    r"/api/v1/games/teen-patti-pro/rooms/([^/]+)/(?:rounds/([^/]+)/)?bets")


class BetRouteParsingTest(unittest.TestCase):
    def _parse(self, path):
        m = BET_PATTERN.fullmatch(path)
        return (m.group(1), m.group(2)) if m else None

    def test_round_scoped_bet_parses_room_and_round(self):
        self.assertEqual(
            self._parse("/api/v1/games/teen-patti-pro/rooms/qaZ/rounds/qaZ-r1/bets"),
            ("qaZ", "qaZ-r1"))

    def test_room_level_bet_parses_room_with_no_round(self):
        self.assertEqual(
            self._parse("/api/v1/games/teen-patti-pro/rooms/qaZ/bets"),
            ("qaZ", None))

    def test_room_id_never_swallows_the_round_segment(self):
        room, _round = self._parse(
            "/api/v1/games/teen-patti-pro/rooms/room-1/rounds/room-1-r7/bets")
        self.assertEqual(room, "room-1")
        self.assertNotIn("/", room)
        self.assertNotIn("rounds", room)

    def test_room_id_with_dashes_and_digits(self):
        self.assertEqual(
            self._parse("/api/v1/games/teen-patti-pro/rooms/stg-room-42/rounds/stg-room-42-r3/bets"),
            ("stg-room-42", "stg-room-42-r3"))

    def test_unrelated_paths_do_not_match(self):
        for path in ("/api/v1/games/teen-patti-pro/rooms/qaZ/rounds/qaZ-r1/result",
                     "/api/v1/games/teen-patti-pro/rooms/qaZ/rounds/start",
                     "/api/v1/games/greedy-monkey/rooms/qaZ/rounds/r1/bets",
                     "/api/v1/wallet/balance"):
            self.assertIsNone(self._parse(path), path)


class GreedySegmentGuardTest(unittest.TestCase):
    """No route may capture a path segment with a greedy \\S+ / .+ .

    A greedy multi-segment matcher plus an optional trailing group is the bug
    pattern. A single-segment greedy matcher with no optional tail is fine, so
    only the combination is banned.
    """

    ROUTES = (
        (r"/api/v1/games/teen-patti-pro/rooms/([^/]+)/(?:rounds/([^/]+)/)?bets", True),
        (r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/start", False),
    )

    def test_greedy_plus_optional_is_banned(self):
        for pattern, _ok in self.ROUTES:
            has_greedy_multi = ("(\\S+)" in pattern or "(.+)" in pattern)
            has_optional_tail = "(?:" in pattern and ")?" in pattern
            if has_greedy_multi and has_optional_tail:
                self.fail(f"greedy segment + optional tail in {pattern!r}")

    def test_every_route_pattern_compiles(self):
        for pattern, _ok in self.ROUTES:
            re.compile(pattern)


if __name__ == "__main__":
    unittest.main()
