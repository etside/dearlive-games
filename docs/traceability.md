# Requirement Traceability Matrix (BRD/SRS → implementation → evidence)

Legend: PASS = implemented + tested. TBC = business-unconfirmed default in
code, explicitly flagged (never silently invented). Run: sandbox local,
`python3 -m unittest discover -s tests` → **76 tests OK** (2026-09-22).

## Common lifecycle + identity + money

| ID | Requirement | Implementation | API | DB | UI | Test | UAT | St |
|---|---|---|---|---|---|---|---|---|
| LC-01 | UPCOMING→OPEN→CLOSED→RESULT PROCESSING→SETTLED→CLOSED | `common/lifecycle.py` (RESULT_PROCESSING + `result.processing` event in both engines) | state/status everywhere | `rounds.status` | status + countdown | `test_engine`, wheel sweep asserts CLOSED | gate 4,5,12 | PASS |
| LC-02 | Server-authoritative identity | launch-token redeem → `Bearer session` (`service.open_session`, `session_player_any`) | `POST sessions`, `POST games/{id}/sessions` | `player` | session via host WebView | e2e, cross-game | gate launch | PASS |
| LC-03 | Unique bet ID + wallet txn ref per accepted bet | `{round}-b{seq}` + `bet:{room}:{key}` refs (`service.place_bet`) | bet responses | `bets.bet_id`, `wallet_transaction` | history shows bet_id | gate 8,16,19 | gate 16,19 | PASS |
| LC-04 | Atomic debit; failed debit = failed bet | debit-before-create; `InsufficientBalance`→402, no row | `402 INSUFFICIENT_BALANCE` | no orphan rows (compensate) | balance unchanged | `test_c5`, gate 9 | gate 9 | PASS |
| LC-05 | Winnings credited exactly once | `UNIQUE settlement.bet_id` + settle lock + idempotent credit | re-settle safe | `settlements UNIQUE(bet_id)`, immutable `wallet_transaction` | balance | gate 17,18 | gate 17,18 | PASS |
| LC-06 | Idempotency on bet/settlement | `bet:{key}` / `settle:{bet_id}` claim→complete; 409 on conflict | `Idempotency-Key` / `idempotencyKey` | `idempotency_keys` | repeat uses new keys | gate 21 | gate 21 | PASS |
| LC-07 | Betting closes on server timestamp | `decision_time < betting_end_at` under room lock; `sweep()` 1/s | `betting_end_at` + `serverTime` | `rounds.betting_end_at_ms` | countdown from serverTime | gate 12 | gate 12 | PASS |
| LC-08 | Player + admin history | player own-bets; admin audit/webhooks/settlements views | `GET history`, `/admin/*` | `game_history`, `audit_log` | HIST panel | gate 20,23 | gate 20,23 | PASS |
| LC-09 | Server-side auditable reproducible results | seed + deck_commit / server_seed reveal per round | result payloads | `game_result`, `rounds.seed_hex` | winner banner | gate 13,14 | gate 13–15 | PASS |
| LC-10 | Common UI controls | Back/Help/Sound/Menu, round no, connection, countdown, balance, denoms, History/Result, Repeat, Auto(wheels) | state/history/recent/autobet | — | teen `game.js`, wheel `client.html` | gate 2,3,6,7 | gate 2,3,6,7 | PASS* |
| LC-11 | Admin config | enable/disable, durations, denoms, min/max, options/images, multipliers, HOT, packages, localization, audit/reports | `GET/PUT /admin/games[/{id}/config]` (PUT superadmin) | `game`, `game_configuration` (versioned) | host admin (this pkg exposes API) | cross-game admin test | admin UAT | PASS |

\* Sound = toggle + win chime; background music loop is TBC (no DearLive asset supplied).

## GAME 1 — Greedy (`greedy-monkey`, G1)

| ID | Requirement | Implementation | API | DB | UI | Test | UAT | St |
|---|---|---|---|---|---|---|---|---|
| G1-FR-01 | Unique round no + synced countdown | `WheelRound.round_no`, `betting_end_at_ms` + serverTime | `rounds/current` | `rounds` | header + ring | wheel flow | round UAT | PASS |
| G1-FR-02 | Configured food/ingredient options | `WheelOption` list (TBC W-OPTS) | state `options`, admin config | `game_option` | circular layout | wheel flow | options UAT | PASS |
| G1-FR-03 | Multiplier + accumulated total/option | `multiplier` + `totals{option}` | state | derived from `bets` | per-option totals | wheel flow | totals UAT | PASS |
| G1-FR-04 | Denom selection + option betting | denoms/min/max validation | `POST rounds/{id}/bets` | `bets` | chips + tap-tap | wheel flow | betting UAT | PASS |
| G1-FR-05 | balance/status/round/min-max/availability validation | `_validate` + balance + window | 402/422/409 codes | — | error messages | wheel invalid tests | invalid-bet UAT | PASS |
| G1-FR-06 | Total Bet + My Total Bet | `total_bet`, `my_total_bet`, `my_totals` | state | derived | header line | wheel flow | totals UAT | PASS |
| G1-FR-07 | Server-controlled betting close | close_betting + sweep | admin close | status | countdown expiry | wheel sweep | closing UAT | PASS |
| G1-FR-08 | Result + winning-option identification | `wheel_outcome` (HMAC, DearLive parity) | `rounds/{id}/result` | `game_result` | winner glow | wheel flow | result UAT | PASS |
| G1-FR-09 | Settlement vs accepted bets | stake×multiplier (TBC W-PAYOUT-RULE), exactly-once | settle | `settlements` | balance | wheel settle | settlement UAT | PASS |
| G1-FR-10 | Auto Bet where approved | `set_autobet` + server-side firing; `auto_allowed` gate | `POST autobet` | config payload | AUTO panel | autobet test | auto UAT | PASS |
| G1-UI | central character, circular layout, balance, denoms, auto, back/help/sound/menu/connection | `games/wheel_common/client.html`, `/greedy-monkey/` | entry + state | — | wheel client | gate-style (client static served) | UI UAT | PASS |

Unspecified G1 result/probability/payout/rounding/Auto rules: NOT invented —
`payout_rule` + `W-*` TBC flags in `WheelConfig`; options/admin-configurable.

## GAME 2 — Wheel (`baby-king`, G2)

Same implementation rows as G1 (shared `WheelService`), plus:

| ID | Requirement | Implementation | API | DB | UI | Test | UAT | St |
|---|---|---|---|---|---|---|---|---|
| G2-FR-09b | Recent-result strip | `room.recent` (cap 20) | `GET results/recent` | derived | strip row | wheel recent | history UAT | PASS |
| G2-FR-10 | My History | per-player bet trail | `GET history` | `game_history` | HIST panel | wheel history | history UAT | PASS |
| G2-FR-11 | Balance + Today's Earnings | `today_earnings` (net, TBC W-EARNINGS) | history `earnings_today` | derived | earnings line | wheel flow | balance UAT | PASS |
| G2-FR-12 | Auto Play where approved | `POST autoplay` (alias of autobet) | autoplay | config | AUTO panel | cross-game alias | auto UAT | PASS |
| G2-UI-HOT | HOT visual support | `hot` flag per option | options/config | `game_option.hot` | HOT badge | config HOT test | UI UAT | PASS |

HOT algorithm, Today's-Earnings definition, Auto Play rules, payout algorithm:
TBC flags (`W-HOT`, `W-EARNINGS`, `W-AUTO`, `W-PAYOUT-RULE`) — current-shaped
defaults only.

## GAME 3 — Teen Patti Pro (PRIORITY #1)

| ID | Requirement | Implementation | API | DB | UI | Test | UAT | St |
|---|---|---|---|---|---|---|---|---|
| G3-FR-01 | Positions A/B/C | fixed seats, validated | bets/state | `game_player_position` | 3 chairs | gate 3 | seats UAT | PASS |
| G3-FR-02 | Server-synced guessing timer | `guess_ms` + serverTime countdown | `betting_end_at` | `rounds` | GUESSING ring | gate 4,5 | timer UAT | PASS |
| G3-FR-03 | Denomination selection (20/100/500/1K) | denoms config (TBC DENOMS) | rules/state | `game_configuration` | chips | gate 7 | denom UAT | PASS |
| G3-FR-04 | Valid position/action betting | seat + denom checks | tables/rooms bets | `bets` | tap-tap | gate 8,9 | betting UAT | PASS |
| G3-FR-05 | Per-position pot + own contribution | `pots{A,B,C}`, `my_bet` | state/tables/state | `game_player_position` | pot labels + POT/YOU | gate 10,11 | pot UAT | PASS |
| G3-FR-06 | Server-side betting closure | lock + sweep | close/sweep | status | timer expiry | gate 12 | closing UAT | PASS |
| G3-FR-07 | Card dealing/display + hand evaluation + winner | server deal, `evaluate_hand`, dead-heat split (TBC G3-BR-01..03) | result | `card_hand`, `game_result` | cards + banner | gate 13–15 | result UAT | PASS |
| G3-FR-08 | Wallet settlement + history | debit/credit exactly-once + history | settle/history | `settlements`, `game_history` | balance | gate 16–20 | settlement UAT | PASS |
| G3-API | tables bets/state, round result, history (spec shapes) | `tables/{id}/bets` (roundId/position/amount/idempotencyKey), `tables/{id}/state` (+players), generic result/history | see `api/openapi.yaml` | — | client uses them | cross-game tables | API UAT | PASS |

TBC G3 rules (variant/ranking, dealing, pot contribution, payout, tie, seats,
timer): flagged in `TeenPattiConfig.tbc`, settlements stamp version; only
mandatory rule (server-side auditable shuffle) is implemented.

## Cross-game contract / DB / NFRs

| ID | Requirement | Where | St |
|---|---|---|---|
| X-01..13 | identity, current round, serverTime, betting_end_at, status, options, totals, validation, idempotency, result, debit/credit, histories, config, auto config, admin config, monitoring, reports, audit | `docs/INTEGRATION.md`, generic router, envelope on all responses | PASS |
| DB-01 | 12 entities, unique round/bet/settlement-per-bet, immutable txns, compensating corrections, versioned config | `db/schema.sql` (+ immutability trigger) | PASS |
| NFR | responsive UI, concurrency (race-fixed + `test_concurrency`), server time, authN/Z, TLS-ready (proxy arch), sticky-room scaling, audit logs, `/health`, sweep-failure monitoring, recovery (replay/compensate), auditability, localization-ready labels, WebView compat | code + `docs/DEPLOYMENT.md` | PASS* |

\* TLS terminates at the DearLive reverse proxy (prod); WebView viewport spec
is business-TBC #12.

## UAT evidence (sandbox, 2026-09-22)

`python3 -m unittest discover -s tests` → **76 tests, OK**, including
`test_acceptance_gate` (25/25), `test_e2e_dearlive_flow`,
`test_cross_game_api`, `test_wheel_service`, `test_dearlive_parity`.
Live boot verified: catalog (9 ids: 3 canonical + 6 aliases), entry pages
(`/teen-patti-pro/`, `/greedy-monkey/`, `/baby-king/`), theme/assets static.
Phases: P1 UI prototype ✓ (clients + HUD) → P2 round/timer/state ✓ →
P3 bet+wallet ✓ → P4 result+settlement ✓ → P5 history+admin+audit+reports ✓ →
P6 fixes (sweep audit gap fixed this session) ✓.
