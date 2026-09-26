"""Provider path routing tests (Step A: wallet routing collision).

`is_provider_path()` decides whether a request is handed to the B2B provider
router or kept on the game API. Getting it wrong in the "too broad" direction
is silent and severe: the request is forwarded, the provider router 404s it,
and the client sees "no such provider route" for a route that plainly exists.

It previously matched the whole /api/v1/wallet/ prefix, which swallowed the
local GET /api/v1/wallet/balance -- the route the game client reads to render
BAL. That produced a null balance.
"""
import re
import unittest

from provider.router import (GAME_SLUG, PROVIDER_PREFIX, ROUTES,
                             is_provider_path)


class ProviderWalletRoutingTest(unittest.TestCase):
    def test_wallet_credit_is_provider_route(self):
        self.assertTrue(is_provider_path("/api/v1/wallet/credit"))

    def test_wallet_debit_is_provider_route(self):
        self.assertTrue(is_provider_path("/api/v1/wallet/debit"))

    def test_wallet_rollback_is_provider_route(self):
        self.assertTrue(is_provider_path("/api/v1/wallet/rollback"))

    def test_wallet_transactions_is_provider_route(self):
        self.assertTrue(is_provider_path("/api/v1/wallet/transactions/txn-1"))

    def test_wallet_balance_is_local_route(self):
        """The bug: this must NOT be forwarded, or BAL renders null."""
        self.assertFalse(is_provider_path("/api/v1/wallet/balance"))

    def test_is_provider_path_does_not_match_wallet_balance(self):
        for path in ("/api/v1/wallet/balance", "/api/v1/wallet",
                     "/api/v1/wallet/history", "/api/v1/wallet/balance/extra"):
            self.assertFalse(is_provider_path(path), path)

    def test_wallet_transactions_requires_an_id(self):
        self.assertFalse(is_provider_path("/api/v1/wallet/transactions"))
        self.assertFalse(is_provider_path("/api/v1/wallet/transactions/"))
        self.assertFalse(is_provider_path("/api/v1/wallet/transactions/a/b"))


class ProviderPathCoverageTest(unittest.TestCase):
    """No registered provider route may be left unrecognised.

    This is the regression guard for the whole bug class: if a route is added
    to ROUTES but not to is_provider_path, it silently becomes unreachable.
    """

    @staticmethod
    def _concrete(pattern: str) -> str:
        """Turn a route regex into one concrete path it must match."""
        s = pattern
        if s.startswith("^"):
            s = s[1:]
        if s.endswith("$") and not s.endswith("\\$"):
            s = s[:-1]
        s = s.replace("\\.", ".").replace("\\/", "/")
        # Parenthesised character classes first: ([^/]+) / ([^/]*)
        s = re.sub(r"\(\[\^/\]\+\)", "x", s)
        s = re.sub(r"\(\[\^/\]\*\)", "x", s)
        s = re.sub(r"\(\.\*\)", "y", s)
        s = re.sub(r"\(\.\+\)", "y", s)
        s = re.sub(r"\(\\S\+\)", "x", s)
        s = re.sub(r"\(\?:", "(", s)
        s = re.sub(r"\(\?P<[^>]*>", "(", s)
        s = re.sub(r"/\?", "", s)                    # optional trailing slash
        # plain group with alternation -> first alternative
        s = re.sub(r"\(([^()]*)\)", lambda m: m.group(1).split("|")[0], s)
        return s

    def test_every_registered_provider_route_is_recognised(self):
        missed = []
        for _method, pattern, _handler, _auth in ROUTES:
            path = self._concrete(pattern.pattern)
            # The synthesised path must actually match the route, otherwise
            # this test would be asserting against a path the route never had.
            if not pattern.match(path):
                missed.append(("SYNTHESIS-BROKEN", pattern.pattern, path))
                continue
            if not is_provider_path(path):
                missed.append(("NOT-RECOGNISED", pattern.pattern, path))
        self.assertEqual(missed, [], "provider routes unreachable: %r" % (missed,))

    def test_provider_prefix_is_recognised(self):
        self.assertTrue(is_provider_path(PROVIDER_PREFIX + "/health"))
        self.assertTrue(is_provider_path(PROVIDER_PREFIX + "/launch/abc"))

    def test_table_routes_are_recognised_for_every_slug(self):
        # Teen Patti only.
        for slug in ("teen-patti", "teen-patti-pro"):
            for suffix in ("/tables", "/tables/t1", "/tables/t1/state",
                           "/tables/t1/action"):
                self.assertTrue(is_provider_path(f"/api/v1/{slug}{suffix}"),
                                f"{slug}{suffix}")

    def test_retired_game_slugs_are_not_provider_routes(self):
        # A retired slug must not match a provider route. It used to, and the
        # request then failed deep inside a handler for an engine that no
        # longer exists, surfacing as a 500 instead of a clean rejection.
        for slug in ("greedy-monkey", "baby-king", "monkey-wheel", "greedy_lion"):
            for suffix in ("/tables", "/tables/t1", "/tables/t1/state",
                           "/tables/t1/action"):
                self.assertFalse(
                    is_provider_path(f"/api/v1/{slug}{suffix}"),
                    f"{slug}{suffix}")

    def test_players_balance_is_recognised_only_with_an_id(self):
        self.assertTrue(is_provider_path("/api/v1/players/p1/balance"))
        self.assertFalse(is_provider_path("/api/v1/players/p1"))
        self.assertFalse(is_provider_path("/api/v1/players/"))


class LocalRouteReachabilityTest(unittest.TestCase):
    """Routes the game client actually calls must stay on the local handler."""

    LOCAL = (
        "/api/v1/wallet/balance",
        "/api/v1/skills",
        "/api/v1/games/teen-patti-pro",
        "/api/v1/games/teen-patti-pro/rounds/current",
        "/api/v1/games/teen-patti-pro/rooms/r1/rounds/start",
        "/api/v1/games/teen-patti-pro/rooms/r1/bets",
        "/api/v1/games/teen-patti-pro/rooms/r1/wallet",
        "/api/v1/admin/whoami",
        "/api/v1/admin/audit",
        "/api/v1/operator/admin/dashboard/kpis",
        "/health",
    )

    def test_local_routes_are_not_forwarded(self):
        forwarded = [p for p in self.LOCAL if is_provider_path(p)]
        self.assertEqual(forwarded, [], "must not forward: %r" % (forwarded,))

    def test_sessions_are_provider_owned_by_design(self):
        """POST /api/v1/sessions is hmac_or_token: the provider owns login."""
        self.assertTrue(is_provider_path("/api/v1/sessions"))
        self.assertTrue(is_provider_path("/api/v1/sessions/abc"))
        self.assertTrue(is_provider_path("/api/v1/games"))


if __name__ == "__main__":
    unittest.main()
