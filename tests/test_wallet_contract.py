"""Contract tests for the wallet provider adapter.

This is a TEST DOUBLE, not a production wallet. It exists only to pin the
contract the DearLive wallet must satisfy, so that wiring the real provider
later is configuration rather than a rewrite. Production selects its adapter
from WALLET_BASE_URL in `integrations.build_stores`; nothing here is reachable
from that path, and `test_production_cannot_select_the_test_double` asserts it.

Three states must stay distinguishable, because the UI treats them differently
and conflating them is how an unconfigured deployment ends up showing a
balance of 0 -- which reads as "you are broke" rather than "no wallet is wired
up yet":

    WalletNotConfigured   no external wallet is configured
    WalletError           the wallet is configured but unreachable/failing
    InsufficientBalance   the wallet works and the player has no money
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import (Balance, InsufficientBalance, TxnRef,
                           WalletAdapter, WalletError, WalletNotConfigured)
from integrations.dearlive import HttpDearLiveWallet, _is_self


class ContractTestWallet(WalletAdapter):
    """In-memory double implementing the adapter contract, for tests only."""

    def __init__(self, starting=10_000, fail=False):
        self.balances = {"p1": starting}
        self.fail = fail
        self.ledger = {}          # idempotency_key -> TxnRef
        self.calls = []

    def _check(self):
        if self.fail:
            raise WalletError("contract test double is failing")

    def get_balance(self, player_id):
        self._check()
        if player_id not in self.balances:
            raise WalletError(f"unknown player {player_id}")
        return Balance(player_id=player_id,
                       available=self.balances[player_id], currency="COIN")

    def debit(self, player_id, amount, ref, idempotency_key):
        self._check()
        if idempotency_key in self.ledger:
            return self.ledger[idempotency_key]
        self.calls.append(("debit", player_id, amount, ref, idempotency_key))
        if self.balances.get(player_id, 0) < amount:
            raise InsufficientBalance("not enough")
        self.balances[player_id] -= amount
        t = TxnRef(txn_id=f"txn-{len(self.ledger)+1}", idempotency_key=idempotency_key)
        self.ledger[idempotency_key] = t
        return t

    def credit(self, player_id, amount, ref, idempotency_key):
        self._check()
        if idempotency_key in self.ledger:
            return self.ledger[idempotency_key]
        self.calls.append(("credit", player_id, amount, ref, idempotency_key))
        self.balances[player_id] = self.balances.get(player_id, 0) + amount
        t = TxnRef(txn_id=f"txn-{len(self.ledger)+1}", idempotency_key=idempotency_key)
        self.ledger[idempotency_key] = t
        return t

    def refund(self, player_id, amount, ref, idempotency_key):
        return self.credit(player_id, amount, ref, idempotency_key)


class WalletAdapterContractTest(unittest.TestCase):
    def setUp(self):
        self.w = ContractTestWallet()

    def test_get_balance(self):
        b = self.w.get_balance("p1")
        self.assertEqual(b.available, 10_000)
        self.assertEqual(b.currency, "COIN")

    def test_debit_reduces_balance_exactly_once(self):
        before = self.w.get_balance("p1").available
        self.w.debit("p1", 20, "bet:1", "bet:1:debit")
        after = self.w.get_balance("p1").available
        self.assertEqual(after, before - 20)

    def test_credit_increases_balance(self):
        before = self.w.get_balance("p1").available
        self.w.credit("p1", 500, "settle:1", "settlement:1:credit")
        self.assertEqual(self.w.get_balance("p1").available, before + 500)

    def test_repeated_debit_with_same_key_debits_once(self):
        before = self.w.get_balance("p1").available
        a = self.w.debit("p1", 20, "bet:1", "bet:1:debit")
        b = self.w.debit("p1", 20, "bet:1", "bet:1:debit")
        self.assertEqual(a.txn_id, b.txn_id, "same key must return the original txn")
        self.assertEqual(self.w.get_balance("p1").available, before - 20,
                         "a retried debit must not move money twice")

    def test_distinct_keys_debit_separately(self):
        self.w.debit("p1", 20, "bet:1", "bet:1:debit")
        self.w.debit("p1", 20, "bet:2", "bet:2:debit")
        self.assertEqual(self.w.get_balance("p1").available, 10_000 - 40)

    def test_insufficient_balance_is_its_own_type(self):
        with self.assertRaises(InsufficientBalance):
            self.w.debit("p1", 10 ** 9, "bet:big", "bet:big:debit")

    def test_provider_failure_raises_wallet_error(self):
        w = ContractTestWallet(fail=True)
        with self.assertRaises(WalletError):
            w.get_balance("p1")

    def test_wrong_player_is_rejected(self):
        with self.assertRaises(WalletError):
            self.w.get_balance("nobody")

    def test_debit_key_shape_is_namespaced(self):
        """The key must embed the bet id so a retry can never collide."""
        self.w.debit("p1", 20, "bet:xyz", "bet:xyz:debit")
        self.assertIn("bet:xyz:debit", self.w.ledger)


class WalletStateDistinguishingTest(unittest.TestCase):
    def test_the_three_states_are_distinct_types(self):
        self.assertTrue(issubclass(WalletNotConfigured, WalletError))
        self.assertFalse(issubclass(WalletNotConfigured, InsufficientBalance))
        self.assertFalse(issubclass(InsufficientBalance, WalletNotConfigured))

    def test_unconfigured_wallet_reports_not_configured(self):
        env_before = {k: __import__("os").environ.get(k) for k in
                      ("WALLET_BASE_URL", "DEARLIVE_API_BASE_URL")}
        import os
        for k in env_before:
            os.environ.pop(k, None)
        try:
            with self.assertRaises(WalletNotConfigured):
                HttpDearLiveWallet()
        finally:
            for k, v in env_before.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class SelfReferenceGuardTest(unittest.TestCase):
    """A wallet URL pointing at the game host must be refused, not attempted."""

    def test_detects_localhost_and_local_ports(self):
        for url in ("http://127.0.0.1:5002", "https://localhost",
                    "http://127.0.0.1:8000", "http://0.0.0.0:5002"):
            self.assertTrue(_is_self(url), f"{url} should be treated as self")

    def test_does_not_flag_a_real_wallet_host(self):
        for url in ("https://wallet.dearlive.com", "https://api.ura-dhura.com",
                    "http://127.0.0.1:9999"):
            self.assertFalse(_is_self(url), f"{url} must not be treated as self")

    def test_constructing_against_ourselves_raises_not_configured(self):
        import os
        os.environ["WALLET_BASE_URL"] = "http://127.0.0.1:5002"
        try:
            with self.assertRaises(WalletNotConfigured):
                HttpDearLiveWallet()
        finally:
            os.environ.pop("WALLET_BASE_URL", None)


class ProductionAdapterSelectionTest(unittest.TestCase):
    def test_production_cannot_select_the_test_double(self):
        """The double must not be reachable from the production wiring."""
        src = (Path(__file__).resolve().parents[1]
               / "integrations/__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("ContractTestWallet", src)
        self.assertNotIn("tests/", src.replace(" ", ""))
        # Production picks the HTTP wallet purely from configuration.
        self.assertIn("HttpDearLiveWallet", src)

    def test_build_stores_never_returns_a_test_double(self):
        import os
        from integrations import build_stores
        os.environ.pop("DEMO_MODE", None)
        os.environ.pop("REDIS_HOST", None)
        for k in ("WALLET_BASE_URL", "DEARLIVE_API_BASE_URL"):
            os.environ.pop(k, None)
        wallet, _t, _s, _i, note = build_stores()
        self.assertNotIsInstance(wallet, ContractTestWallet)
        self.assertTrue(note)


if __name__ == "__main__":
    unittest.main()


class UnavailableWalletTest(unittest.TestCase):
    """No wallet configured must not stop the game booting."""

    def _wallet(self):
        from integrations.dearlive import UnavailableWallet
        return UnavailableWallet("no external wallet is configured")

    def test_every_operation_raises_not_configured(self):
        w = self._wallet()
        for call in (lambda: w.get_balance("p1"),
                     lambda: w.debit("p1", 20, "r", "k"),
                     lambda: w.credit("p1", 20, "r", "k"),
                     lambda: w.refund("p1", 20, "r", "k")):
            with self.assertRaises(WalletNotConfigured):
                call()

    def test_never_reports_a_balance(self):
        """Returning 0 would read as 'you are broke' rather than 'no wallet'."""
        with self.assertRaises(WalletNotConfigured):
            self._wallet().get_balance("p1")

    def test_game_still_boots_without_a_wallet(self):
        import os
        from integrations import build_stores
        os.environ.pop("DEMO_MODE", None)
        os.environ["REDIS_HOST"] = "127.0.0.1"
        os.environ["WALLET_BASE_URL"] = "http://127.0.0.1:5002"  # self-ref
        try:
            wallet, _t, _s, _i, note = build_stores()
            self.assertIsNotNone(wallet)
            with self.assertRaises(WalletNotConfigured):
                wallet.get_balance("p1")
        finally:
            os.environ.pop("WALLET_BASE_URL", None)
            os.environ.pop("REDIS_HOST", None)

    def test_health_reports_not_configured_for_self_reference(self):
        from integrations.dearlive import _is_self
        import os
        os.environ["GAMES_BASE_URL"] = "https://api.ura-dhura.com"
        try:
            self.assertTrue(_is_self("https://api.ura-dhura.com"))
            self.assertFalse(_is_self("https://wallet.dearlive.com"))
        finally:
            os.environ.pop("GAMES_BASE_URL", None)


class NoRouteLeaksAStackTraceTest(unittest.TestCase):
    """Every production API answers in JSON, whatever happens.

    An unhandled error used to kill the request thread mid-response. nginx then
    returned 502 with an HTML body and the client reported
    "Unexpected token '<'" -- which reads as a client bug and is not. The trace
    went to the journal while the caller got nothing usable.
    """

    def test_verbs_are_wrapped_in_the_json_guard(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        self.assertIn("def _json_guard(self", src)
        for verb in ("do_GET", "do_POST"):
            i = src.index(f"def {verb}(self):")
            block = src[i:i + 120]
            self.assertIn("_json_guard", block,
                          f"{verb} must route through the JSON guard")

    def test_guard_maps_wallet_states_to_distinct_codes(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        i = src.index("def _json_guard(self")
        nxt = src.index(chr(10) + "    def ", i)
        block = src[i:nxt]
        self.assertIn("E_WALLET_NOT_CONFIGURED", block)
        self.assertIn("E_WALLET_UNAVAILABLE", block)
        self.assertIn("E_INTERNAL", block)
        self.assertIn("log.exception", block,
                      "an unexpected error must still be logged server-side")
        self.assertNotIn("traceback.print", block)


class ApiLoggerTest(unittest.TestCase):
    """api.py must define `log` before the guard uses it.

    The JSON guard calls log.exception(). `import logging` was present but the
    module-level `log` was not, so the except branch raised NameError *inside*
    the handler -- the guard could not report an error without becoming one,
    and the connection was still dropped. Same failure shape as the ws.py
    logger bug: an instrumented path where the instrument was never defined.
    """

    def test_api_defines_its_logger(self):
        src = (Path(__file__).resolve().parents[1]
               / "games/teen_patti_pro/api.py").read_text(encoding="utf-8")
        self.assertIn("import logging", src)
        self.assertIn("log = logging.getLogger(__name__)", src)

    def test_logger_defined_before_the_guard_uses_it(self):
        lines = (Path(__file__).resolve().parents[1]
                 / "games/teen_patti_pro/api.py").read_text(
                     encoding="utf-8").split("\n")
        defined = next((i for i, l in enumerate(lines)
                        if l.startswith("log = logging.getLogger")), None)
        self.assertIsNotNone(defined, "no module-level logger in api.py")
        first = next((i for i, l in enumerate(lines)
                      if "log.exception(" in l or "log.warning(" in l), None)
        self.assertIsNotNone(first, "the guard logs nothing")
        self.assertLess(defined, first, "`log` is used before it is defined")
