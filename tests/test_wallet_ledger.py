"""The wallet ledger (SRS section 9).

The in-memory wallet previously kept only balances, which made a discrepancy
undiagnosable: with no transaction history there is no way to tell a rounding
bug from a lost payout. These tests pin the properties that make the ledger
worth keeping -- append-only, reconciling, and idempotent under replay.
"""
import threading
import unittest

from common.wallet import (TXN_ADMIN_CREDIT, TXN_BET_DEBIT, TXN_TYPES,
                           TXN_WIN_CREDIT, InsufficientBalance, MemoryWallet,
                           WalletError)


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.w = MemoryWallet()
        self.w.fund("p1", 1000)

    def test_funding_is_recorded_in_the_ledger(self):
        # Otherwise the balance would not reconcile against its own sum.
        rows = self.w.transactions("p1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], TXN_ADMIN_CREDIT)
        self.assertEqual(rows[0]["amount"], 1000)

    def test_a_debit_is_a_negative_ledger_row(self):
        self.w.debit("p1", 100, "bet-1", "k1")
        row = [r for r in self.w.transactions("p1")
               if r["type"] == TXN_BET_DEBIT][0]
        self.assertEqual(row["amount"], -100)

    def test_a_credit_is_a_positive_ledger_row(self):
        self.w.credit("p1", 250, "win-1", "k1")
        row = [r for r in self.w.transactions("p1")
               if r["type"] == TXN_WIN_CREDIT][0]
        self.assertEqual(row["amount"], 250)

    def test_balance_equals_the_sum_of_the_ledger(self):
        self.w.debit("p1", 100, "bet-1", "k1")
        self.w.credit("p1", 250, "win-1", "k2")
        self.assertEqual(self.w.get_balance("p1").available, 1150)
        v = self.w.verify_balance("p1")
        self.assertTrue(v["reconciled"], v)
        self.assertEqual(v["ledger_sum"], 1150)
        self.assertEqual(v["difference"], 0)

    def test_a_replayed_debit_does_not_twice_move_money(self):
        first = self.w.debit("p1", 100, "bet-1", "same-key")
        self.w.debit("p1", 100, "bet-1", "same-key")
        self.w.debit("p1", 100, "bet-1", "same-key")
        self.assertEqual(self.w.get_balance("p1").available, 900)
        debits = [r for r in self.w.transactions("p1")
                  if r["type"] == TXN_BET_DEBIT]
        self.assertEqual(len(debits), 1, "the ledger must not grow on replay")
        self.assertEqual(debits[0]["txn_id"], first.txn_id)

    def test_a_replayed_credit_does_not_twice_pay(self):
        self.w.credit("p1", 250, "win-1", "same-key")
        self.w.credit("p1", 250, "win-1", "same-key")
        # 1000 funded + ONE 250. Two would be 1500, which is the double
        # payout this whole mechanism exists to prevent.
        self.assertEqual(self.w.get_balance("p1").available, 1250)
        self.assertEqual(len([r for r in self.w.transactions("p1")
                              if r["type"] == TXN_WIN_CREDIT]), 1)

    def test_a_failed_debit_writes_no_ledger_row(self):
        with self.assertRaises(InsufficientBalance):
            self.w.debit("p1", 10_000_000, "bet-big", "k")
        # A rejected bet must not leave a row: the ledger is the record of what
        # actually moved, not of what was attempted.
        self.assertEqual(len([r for r in self.w.transactions("p1")
                              if r["type"] == TXN_BET_DEBIT]), 0)
        self.assertTrue(self.w.verify_balance("p1")["reconciled"])

    def test_an_unknown_transaction_type_is_refused(self):
        with self.assertRaises(WalletError):
            self.w._append("p1", "SLIPPAGE", 10, "x", "k")

    def test_every_srs_transaction_type_is_accepted(self):
        # SRS section 9 lists eight types. All eight must be writable, so an
        # integrator implementing a withdrawal flow does not hit a rejection on
        # a type the spec named. No reconciliation assertion here: _append is
        # the low-level write and deliberately does not move the balance, so
        # calling it directly is expected to diverge.
        for kind in TXN_TYPES:
            self.w._append("p1", kind, 1, "r", f"k-{kind}")
        written = {r["type"] for r in self.w.transactions("p1", limit=500)}
        self.assertEqual(written, set(TXN_TYPES))

    def test_transactions_are_newest_first(self):
        self.w.debit("p1", 20, "b1", "k1")
        self.w.credit("p1", 20, "c1", "k2")
        types = [r["type"] for r in self.w.transactions("p1")]
        self.assertEqual(types, [TXN_WIN_CREDIT, TXN_BET_DEBIT, TXN_ADMIN_CREDIT])

    def test_limit_is_clamped(self):
        for i in range(20):
            self.w._append("p1", TXN_BET_DEBIT, -1, f"r{i}", f"k{i}")
        self.assertEqual(len(self.w.transactions("p1", limit=5)), 5)
        # An unbounded statement is a memory and disclosure problem.
        self.assertLessEqual(len(self.w.transactions("p1", limit=10_000)), 500)
        self.assertGreaterEqual(len(self.w.transactions("p1", limit=0)), 1)

    def test_statements_are_scoped_to_one_player(self):
        self.w.fund("p2", 500)
        self.w.debit("p1", 100, "b1", "k1")
        for row in self.w.transactions("p1"):
            self.assertEqual(row["player_id"], "p1")
        self.assertEqual(len(self.w.transactions("p2")), 1)

    def test_an_empty_ledger_reconciles(self):
        w = MemoryWallet()
        v = w.verify_balance("nobody")
        self.assertTrue(v["reconciled"])
        self.assertEqual(v["transaction_count"], 0)
        self.assertEqual(w.transactions("nobody"), [])

    def test_concurrent_debits_stay_reconciled(self):
        # A plain Lock here deadlocked the process; the ledger is written from
        # inside the same critical section as the balance.
        w = MemoryWallet()
        w.fund("race", 10_000)
        errors = []

        def worker(n):
            try:
                for i in range(20):
                    w.debit("race", 10, f"b{n}-{i}", f"k{n}-{i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertFalse([t for t in threads if t.is_alive()], "deadlock")
        v = w.verify_balance("race")
        self.assertTrue(v["reconciled"], v)
        self.assertEqual(v["balance"], 10_000 - 8 * 20 * 10)
        self.assertEqual(v["transaction_count"], 1 + 8 * 20)


class WalletAdapterContractTest(unittest.TestCase):
    def test_base_adapter_declares_transactions_as_optional(self):
        from common.wallet import WalletAdapter
        self.assertTrue(hasattr(WalletAdapter, "transactions"))

    def test_optional_method_raises_not_implemented_by_default(self):
        from common.wallet import WalletAdapter
        with self.assertRaises(NotImplementedError):
            WalletAdapter().transactions("p1")

    def test_unknown_type_constant_does_not_exist(self):
        # Guards against a typo'd constant silently becoming valid.
        self.assertNotIn("SLIPPAGE", TXN_TYPES)


if __name__ == "__main__":
    unittest.main()
