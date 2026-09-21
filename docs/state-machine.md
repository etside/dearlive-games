# Teen Patti Pro — State Machine + API Design (DRAFT for JEV review #2)

## 1. Round lifecycle (common, BRD SRS 3.1)
`UPCOMING → BETTING_OPEN → BETTING_CLOSED → RESULT → SETTLED → CLOSED`
- `CLOSED` terminal. `cancel()` allowed from UP/BETTING_OPEN/BETTING_CLOSED → CLOSED with void-debit compensation path.
- All transitions server-side, monotonic server timestamps, every transition audit-logged with (round_id, from, to, serverTime).
- Illegal transition → `STATE_CONFLICT`, never silent.

## 2. Betting window (server-authoritative; JEV design-review FLAG late_bet_race 0.78 — FIXED)
- Bets accepted ONLY when status == BETTING_OPEN AND decision_time < betting_end_at (server clock).
- CLOSE vs BET race eliminated by construction: a per-round mutex serializes the
  FULL bet-decision sequence (status re-check → window re-check → idempotency-claim →
  debit → create) against the close procedure (which flips status FIRST under the
  same mutex, then records close_time). No bet can interleave with close.
- Every accepted bet is stamped with decision_time (server ms). Invariant enforced
  at create: decision_time < betting_end_at, else reject WITHOUT debit.
- Late arrival (network delay) → `BETTING_CLOSED` rejection with serverTime + betting_end_at echoed so client can calibrate drift.
- Timer broadcast: WS `round.tick` every 1s (serverTime, ends_in_ms); client renders countdown from server values only.

## 3. Bet placement order (non-negotiable)
auth → game → round → action → amount → min/max → balance → window → idempotency-claim → atomic debit → create bet → publish `bet.accepted`.
- Idempotency: `Idempotency-Key` header REQUIRED on POST bet. Same key+same hash → replay stored betId. Same key+different hash → `DUPLICATE_REQUEST`/409 conflict, no second debit.
- Debit-before-create: if debit raises InsufficientBalance → `INSUFFICIENT_BALANCE`, no bet row. If process dies between debit and bet-create, recovery job voids orphan debits (compensating txn; ledger stays append-only).

## 4. Teen Patti round flow (Game 1)
1. `startRound(table)`: status UPCOMING; seats A/B/C assigned (config: fixed 3 seats, TBC if variable).
2. open betting: BETTING_OPEN, betting_end_at = now + guess_ms (config, TBC default 20000).
3. Players bet: chip denom ∈ config denoms (TBC default [20,100,500,1000]) on position A/B/C. Multi-bet allowed (each = separate bet row + idempotency key). Repeat replays last round's bets as NEW keys.
4. Timer expiry (server): BETTING_CLOSED; late bets rejected.
5. `deal()`: server RNG (seeded CSPRNG; seed + shuffle proof stored in game_result.result_ref for audit) deals 3 cards × 3 positions from single 52-deck, no replacement. Cards hidden until RESULT.
6. `evaluate()`: rank per config ranking table (TBC default standard: trail > pure-seq > seq > color > pair > high; A-K-Q high / 2-3-5 low). Winner = best hand; ties → split pot equally, remainder 1 unit to earliest seat (TBC default, flagged).
7. `settle()`: pot = Σ bets + carry_over (from prior ties); payout winner share; each bet settled EXACTLY once (UNIQUE settlement.bet_id); winnings credited idempotently; status SETTLED → CLOSED. Losers: settlement row with payout 0 (traceability).
   Tie split (JEV money-out FLAG split_unfair 0.89 — FIXED): dead-heat pro-rata by
   stake across ALL winning bets (industry standard). Equal-per-position was rejected:
   it could pay a winning side LESS than staked. Floor dust -> carry_out (no seat bias).
   No-bets-on-winners -> whole distributable carried. TBC G3-BR-03; business may choose
   house-take instead via config.
8. Cancel path: void all debits via compensating credits, mark bets voided, audit.

## 5. Reconnection (designed — BRD has no spec, JEV-flagged)
- Client reconnects with session_id + last_seen_event_seq. Server responds with full snapshot (round status, timer, pots, own bets, cards-visibility per phase) + missed events since seq (action log per round, capped 500).
- Rejoin during BETTING_OPEN: may bet. During/after RESULT: read-only for that round. Cards never leak early: snapshot redacts hole cards until RESULT.

## 6. REST (v1, envelope; auth Bearer session)
- GET /api/v1/games | GET /api/v1/games/teen-patti-pro
- POST /api/v1/sessions (token redeem) | GET /api/v1/sessions/{id} | POST .../join | POST .../leave
- GET /api/v1/games/teen-patti-pro/rounds/current
- POST /api/v1/games/teen-patti-pro/rounds/{id}/bets (Idempotency-Key required)
- GET .../rounds/{id}/result | GET .../history?player=&limit=&cursor=
- POST .../repeat (replay last bets)
- Admin/*: config get/put (versioned), rooms, players, bets, results, settlements, audit, webhook status — all RBAC + audited.

## 7. WebSocket events (room channel, serverTime on all)
round.started | round.tick | bet.accepted | bet.rejected | betting.closed | cards.dealt (visibility-safe) | result.published | settlement.completed | session.completed | player.joined | player.left | error
- Each event carries event_seq (per-round monotonic) for gap detection + replay.
- Duplicate-action protection: client action_id echoed; server dedupes by (round_id, action_id).

## 8. Authority boundaries (JEV is ADVISORY only)
Server authoritative: RNG, deal, rank, timer, window, balance moves (via wallet), result, settlement. Client authoritative: NOTHING financial. JEV reviews designs/code/tests; JEV output never enters game state.
