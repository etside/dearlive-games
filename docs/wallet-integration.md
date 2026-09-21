# Wallet integration [CLIENT API REQUIRED]

Engine never touches money. Map `common/wallet.py WalletAdapter` (5 methods:
`get_balance, debit, credit, void_debit` + idempotent `TxnRef`) to prod DearLive
endpoints and record the mapping table here before any real-money round.

Requirements on the prod mapping: debit must be atomic + idempotent on
`(ref, idempotency_key)`; credit idempotent per settlement ref; ledger append-only
(void via compensating credit, never delete); integer minor units only.
Money order enforced by service: balance-check → idempotency-claim → debit →
create → publish; settle loop under `_settle_lock` with UNIQUE settlement.bet_id.
Until mapped + load-tested, run `confirmed=False` (E_TBC_BLOCKED on money paths).
