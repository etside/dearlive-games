DESIGN UNDER REVIEW: Teen Patti Pro state machine + API (docs/STATE-MACHINE.md + common/envelope.py, lifecycle.py, idempotency.py, wallet.py, session.py). Game 1 only.

PROPOSED:
1. Lifecycle UPCOMING→BETTING_OPEN→BETTING_CLOSED→RESULT→SETTLED→CLOSED, CLOSED terminal, cancel→CLOSED with void-debit compensation. Illegal transition raises, never silent.
2. Bets accepted ONLY in BETTING_OPEN + now<betting_end_at (server clock). Late bets rejected with serverTime+betting_end_at echoed for drift calibration. WS round.tick 1s with server values; client never uses local clock.
3. Bet order: auth→game→round→action→amount→min/max→balance→window→idempotency-claim→atomic-debit→create-bet→publish. Idempotency-Key header REQUIRED; same key+hash replays betId; same key+different hash → 409 no second debit. Debit-before-create; orphan-debit recovery via compensating void (append-only ledger).
4. Deal: server CSPRNG, seed+shuffle proof stored in result_ref. 3 cards x 3 seats, single 52-deck no replacement. Evaluate standard ranking default (TBC-flagged). Winner pot-takes-all; ties split equally, 1-unit remainder to earliest seat (TBC default).
5. Settle exactly once per bet (UNIQUE settlement.bet_id); losers get payout-0 settlement rows; winnings idempotent credit.
6. Reconnect: session_id + last_seen_event_seq → full snapshot (hole cards redacted pre-RESULT) + missed events from per-round action log (cap 500). Rejoin in BETTING_OPEN may bet; post-RESULT read-only.
7. WS events carry per-round monotonic event_seq for gap detection; client action_id deduped by (round_id, action_id).
8. JEV advisory only; server authoritative for RNG/cards/timer/balance/result/settlement.
9. TBC rules versioned-config-gated with config_version stamped on settlements; real-money blocked unless config confirmed (E_TBC_BLOCKED).
