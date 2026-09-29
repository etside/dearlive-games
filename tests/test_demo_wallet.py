"""Demo wallet acceptance: guard, bet flow, exactly-once settlement.

DemoWalletAdapter is in-memory test money for demo/staging Teen Patti play.
It enforces balances and idempotency exactly like a real ledger would, so the
bet/settle code paths exercised here are the same ones production uses. No
database, no network, no DearLive dependency.
"""
import os
import unittest
from dataclasses import replace

from common.wallet import InsufficientBalance
from games.teen_patti_pro.config import DEFAULT_CONFIG
from games.teen_patti_pro.service import TeenPattiService
from integrations.wallet_demo import DemoWalletAdapter


def _demo_service(opening=10_000):
    cfg = replace(DEFAULT_CONFIG, confirmed=True)
    return TeenPattiService(wallet=DemoWalletAdapter(starting_balance=opening),
                            config=cfg)


def _seat_and_bet_all(svc, room_id, player_id, per_seat):
    """Seat a player and bet every seat.

    The engine deals to ALL configured seats, including empty ones, so a
    single-seat bet can lose to a phantom hand. Betting all three seats makes
    the pot accounting deterministic: rake is 0 and the pro-rata dead-heat
    rule pays the whole distributable to the winning bets, so a clean run
    returns the player to their opening balance.
    """
    svc.sessions.create(player_id, room_id, "teen-patti-pro")
    svc._room(room_id).create_session(player_id)
    svc.start_round(room_id)
    for i, seat in enumerate(svc.config.seats):
        svc.place_bet(room_id, player_id, seat, per_seat, f"k-{player_id}-{i}")


class DemoWalletGuardTest(unittest.TestCase):
    def test_demo_wallet_refuses_production(self):
        old = os.environ.get("APP_ENV")
        os.environ["APP_ENV"] = "production"
        try:
            with self.assertRaises(RuntimeError):
                DemoWalletAdapter()
        finally:
            if old is None:
                os.environ.pop("APP_ENV", None)
            else:
                os.environ["APP_ENV"] = old

    def test_factory_selects_demo_wallet_when_flagged(self):
        from integrations import build_stores
        saved = {k: os.environ.get(k) for k in
                 ("APP_ENV", "DEMO_WALLET_ENABLED", "DEMO_MODE",
                  "WALLET_BASE_URL", "DEARLIVE_API_BASE_URL")}
        os.environ["APP_ENV"] = "sandbox"
        os.environ["DEMO_WALLET_ENABLED"] = "true"
        os.environ.pop("DEMO_MODE", None)
        # Self-referential URL: HttpDearLiveWallet refuses it, and the demo
        # flag must select the demo adapter instead of UnavailableWallet.
        os.environ["WALLET_BASE_URL"] = "http://127.0.0.1:5002"
        os.environ.pop("DEARLIVE_API_BASE_URL", None)
        try:
            wallet, _t, _s, _i, note = build_stores()
            self.assertIsInstance(wallet, DemoWalletAdapter, note)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_no_flag_no_demo_wallet(self):
        from integrations import build_stores
        from integrations.dearlive import UnavailableWallet
        saved = {k: os.environ.get(k) for k in
                 ("APP_ENV", "DEMO_WALLET_ENABLED", "DEMO_MODE",
                  "WALLET_BASE_URL", "DEARLIVE_API_BASE_URL")}
        os.environ["APP_ENV"] = "sandbox"
        os.environ.pop("DEMO_WALLET_ENABLED", None)
        os.environ.pop("DEMO_MODE", None)
        os.environ["WALLET_BASE_URL"] = "http://127.0.0.1:5002"
        os.environ.pop("DEARLIVE_API_BASE_URL", None)
        try:
            wallet, _t, _s, _i, _note = build_stores()
            self.assertIsInstance(wallet, UnavailableWallet)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class DemoBetFlowTest(unittest.TestCase):
    def test_single_bet_debits_exactly_100(self):
        svc = _demo_service()
        self.assertEqual(svc.wallet.get_balance("demo_001").available, 10_000)
        svc.sessions.create("demo_001", "r1", "teen-patti-pro")
        svc._room("r1").create_session("demo_001")
        svc.start_round("r1")
        svc.place_bet("r1", "demo_001", "A", 100, "bet-1")
        self.assertEqual(svc.wallet.get_balance("demo_001").available, 9_900)

    def test_duplicate_debit_key_returns_cached_no_second_debit(self):
        svc = _demo_service()
        # Two direct debits under ONE key: the second must replay the first.
        t1 = svc.wallet.debit("demo_001", 100, "bet:r1:bet-dup", "bet-dup")
        before = svc.wallet.get_balance("demo_001").available
        t2 = svc.wallet.debit("demo_001", 100, "bet:r1:bet-dup", "bet-dup")
        self.assertEqual(t1.txn_id, t2.txn_id)
        self.assertEqual(svc.wallet.get_balance("demo_001").available, before)

    def test_insufficient_balance_rejected_no_negative(self):
        svc = _demo_service(opening=50)
        with self.assertRaises(InsufficientBalance):
            svc.wallet.debit("demo_001", 100, "bet:big", "bet-big")
        self.assertEqual(svc.wallet.get_balance("demo_001").available, 50)

    def test_full_round_settles_and_replay_pays_once(self):
        svc = _demo_service()
        _seat_and_bet_all(svc, "r1", "demo_001", 100)
        self.assertEqual(svc.wallet.get_balance("demo_001").available,
                         10_000 - 300)
        room = svc._room("r1")
        room.close_betting(svc._now())
        room.calculate_result(svc._now())
        before = svc.wallet.get_balance("demo_001").available
        out1 = svc.settle("r1", room.round.round_id)
        after_first = svc.wallet.get_balance("demo_001").available
        # Dead-heat rule returns the whole distributable: back to opening.
        self.assertEqual(after_first, 10_000)
        self.assertTrue(out1.get("credited") or out1.get("rows")
                        or after_first != before)
        out2 = svc.settle("r1", room.round.round_id)
        after_second = svc.wallet.get_balance("demo_001").available
        self.assertEqual(after_second, after_first,
                         "replaying settlement must not pay twice")

    def test_rollback_reverses_a_debit(self):
        w = DemoWalletAdapter()
        t = w.debit("demo_001", 100, "bet:x", "bet-x")
        self.assertEqual(w.get_balance("demo_001").available, 9_900)
        r = w.rollback("demo_001", t.txn_id)
        self.assertTrue(r["success"])
        self.assertEqual(w.get_balance("demo_001").available, 10_000)
        r2 = w.rollback("demo_001", t.txn_id)
        self.assertTrue(r2.get("already_reversed"))
        self.assertEqual(w.get_balance("demo_001").available, 10_000)


if __name__ == "__main__":
    unittest.main()
