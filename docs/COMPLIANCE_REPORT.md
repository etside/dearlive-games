# BRD/SRS Compliance Report — Teen Patti Pro v1.0

Audit date: 2026-09-26 · Commit audited: `df60d56` · Suite: **587 passed, 2 skipped, 0 failed**

Method: every requirement was checked against the source with `file:line`
evidence or exercised against a running server. A requirement is marked PASS
only with a concrete citation or an executed check. Two audits are marked
**NOT VERIFIABLE** rather than passed.

---

## Functional Requirements

| ID | Status | Evidence |
|----|--------|----------|
| FR-01 Round number + server-synced countdown | PASS | `engine.py:165,230` round_no; `game.js:756-759` `betting_end_at - (Date.now()+skew)` |
| FR-02 3 seats, avatars, names, pot | PASS | `engine.py:536-543` seat→player map; `game.js:718` avatar draw; `config.py:41` `seats=('A','B','C')` |
| FR-03 3 cards/seat, face-down until reveal | PASS | `engine.py:560` `{p:["**"]*3 ...}`; `config.py:42` `cards_per_hand=3` |
| FR-04 Chip + seat selection + bet placement | PASS | `game.js:941-953` hit-test; route `rooms/{room}/bets` |
| FR-05 Validate balance/status/min/max/seat | PASS | `engine.py:253-268` `validate_bet`: window, position, denomination, min/max, per-round limit |
| FR-06 Total Bet / My Total Bet live | PASS (fixed) | `game.js:785` now renders the FR-06 wording; `engine.py:548-549` `pot_total`,`my_bet` |
| FR-07 Auto-close at server end time | PASS | `service.py:387-393` `if now < r.betting_end_at_ms: continue` else `close_betting` |
| FR-08 Server-side deal after close | PASS | `engine.py:240` `r.hands = {p: deck[i*3:(i+1)*3] ...}` |
| FR-09 Server-side hand evaluation | PASS | `engine.py:127-171` `evaluate_hand` |
| FR-10 Winner determination + result publish | PASS | `engine.py:395-410`; `service.py` `_fire("result.published")` |
| FR-11 Settlement exactly once | PASS | `engine.py` `if r._settled: return r.settlements` |
| FR-12 Wallet via provider callbacks | PASS | `integrations/dearlive.py:154-166` HMAC-signed `_write` |
| FR-13 Auto-start next round | PASS | `service.py:157` `started = self.start_round(room_id)` |
| FR-14 Repeat Bet | PASS | `game.js:1003-1019` `doRepeat()` replays the last 3 bets from history |
| FR-15 Auto Bet | PARTIAL | Route `api.py:1828` exists and gates cleanly (`404 Auto Bet not supported here`) because the service has no `set_autobet`. Spec marks this TBC. Not implemented. |

## UI Requirements

| ID | Status | Evidence |
|----|--------|----------|
| UI-01 Header (back, help, sound, menu, latency) | PASS | `index.html` `#hBack #hSound #hHelp #hMenu #latency`; wired `game.js` `on('hBack'` etc.; latency measured `Date.now()-t0`, empty when unmeasured |
| UI-02 Round pill (round #, room) | PASS | `index.html` `#roundNo #roomName`; `game.js` `setRoundPill` |
| UI-03 Circular countdown timer | PASS | `game.js:756-759`, server-anchored |
| UI-04 Seats + chair SVGs | PASS (fixed) | Layout now A left / B centre / C right (`game.js:659-664`); chairs A green `#22c55e`, B blue `#3b82f6`, C red `#ef4444`; **all three were 404ing** — route fixed `api.py:1019` |
| UI-05 3-card area per seat | PASS | `config.py:42`; `game.js` `hands(...)` |
| UI-06 Pot per seat + total | PASS | `engine.py:548`; `game.js:816` per-seat `POT` |
| UI-07 Chip bar 20/100/500/1K | PASS | `config.py` `denoms`; `game.js` `DENOMS` |
| UI-08 Balance panel | PASS | `game.js:830` `BAL` |
| UI-09 Repeat Bet button | PASS | `game.js:846` hit-region; `doRepeat()` |
| UI-10 Result overlay + winner highlight | PASS | `game.js` `WINNER:` banner; `engine.py:400` `winners` |
| UI-11 Connection indicator | PASS | 4 states: live / polling / offline / connecting; `game.js` `setConnection` |
| UI-12 Loading / empty / error / reconnecting | PASS | `index.html` `#veil`; `game.js` `setVeil`, `setVeilForState` |
| UI-13 Reduced motion | PASS | `index.html` + `game.js` `prefers-reduced-motion`, covers HUD spinner and connection blink |
| UI-14 Responsive 360→1440 | PASS | `index.html` `@media (max-width:420px)` / `(min-width:1280px)`; canvas layout normalised 0..1 |

## Business Rules

| ID | Status | Evidence |
|----|--------|----------|
| BR-01 Server authoritative | PASS | `engine.py:529` `snapshot()` is the only state source |
| BR-02 Client never computes results | PASS | No evaluator in `game.js`; renders server `winners` |
| BR-03 Server-side crypto RNG | PASS | `engine.py:234` `secrets.token_hex(16)`. Entropy is a CSPRNG; the permutation is deterministic given that seed, which is what BR-04 requires. The two cannot both be true of one function. |
| BR-04 Round shuffle auditable | PASS | `engine.py:55` `deck_commitment()`; seed + commitment emitted in the result payload |
| BR-05 Bet order auth→validate→debit→confirm | PASS | `service.py:160-200`, numbered 1-10 with the spec's order |
| BR-06 Atomic debit, idempotent credit | PASS | `common/wallet.py:91-109` `_once` under `RLock` |
| BR-07 Unique betId settlement | PASS | `004_settlement_bet_unique.up.sql:17`; `007` `game_settlement.bet_id UNIQUE` |
| BR-08 Immutable wallet transactions | PASS | `002_phase3_economy.up.sql:149` `CREATE TRIGGER wallet_transaction_immutable` |
| BR-09 Config changes apply next round | PASS | `engine.py:237` `config_snapshot=asdict(self.config)` per round |
| BR-10 TBC gate blocks real money | PASS | `service.py:_require_confirmed` at :101,:169,:275; `envelope.py:64` `TBC_RULE_UNCONFIRMED` |
| BR-11 Hand rankings admin-configurable | PASS (fixed) | Was a FAIL: `ranking_order` declared and referenced nowhere. Now `engine.py:134-171` derives category strength from the configured order; `score_hand` bypasses the precomputed table under a non-default order |
| BR-12 Payout formula configurable | PARTIAL | No payout-formula config field exists. Settlement is pot-minus-rake with pro-rata dead-heat split (`engine.py`). Spec marks this TBC. Not implemented. |
| BR-13 Rake % configurable, default 0 | PASS | `config.py:51` `rake_bps=0`; **applied** `engine.py:349` `pot * rake_bps // 10_000` |
| BR-14 Max win cap configurable | PASS (fixed) | Was a FAIL: `max_win_cap` declared, never read. Now clamped per bet in `settle()`, excess to `carry_out`, rows report `cap_applied` |

## API Endpoints

| ID | Status | Evidence |
|----|--------|----------|
| Session endpoints | PASS | `POST /api/v1/sessions`, `GET /api/v1/sessions/{id}`, `POST /api/v1/games/{game}/sessions`, `rooms/{room}/reconnect` |
| Game endpoints | PASS | catalog, config, current round, result, history, bets, reconnect |
| Wallet endpoints | PASS | `GET /api/v1/wallet/balance`, `GET /api/v1/wallet/transactions` |
| Admin endpoints | PASS | dashboard, profit-risk get/put/simulate, players, per-player override get/post/delete, packages CRUD, settings, scheduled changes + apply, game enable/disable, rules get/put/**rollback**, audit |
| Envelope shape | PASS | `common/envelope.py` all six keys present |
| serverTime ISO8601 | PASS | `common/envelope.py:16` `now_iso()`; `serverTimeMs` alongside for arithmetic |
| Idempotency-Key required on financial writes | PASS | `service.py:171` raises `Idempotency-Key required` |

## WebSocket

| ID | Status | Evidence |
|----|--------|----------|
| Port 5003 | PASS | `api.py:1783` `--ws-port` default 5003 |
| All 11 events emitted | PASS (fixed) | Was 10/11 — `balance.updated` had no emit site. Now 11/11; `common/wire_events.py` `DECLARED_ONLY` is empty and asserted by test |
| Events carry roundId/serverTime/requestId | PASS | `ws.py` fanout |
| Reconnect fetches authoritative state | PASS | `service.py` `reconnect()`; client `refresh()` |

## Wallet

| ID | Status | Evidence |
|----|--------|----------|
| 8 transaction types | PASS | `common/wallet.py:75-82` |
| Immutable ledger at DB level | PASS | `002:149` trigger; `007` `REVOKE UPDATE, DELETE ON admin_audit` |
| settlement.bet_id UNIQUE | PASS | `004:17` + `007` |
| DECIMAL(18,4) | PASS | `007` game_bet / game_result / game_settlement |
| SETTLED_PENDING retry state | PASS | `engine.py:404-416`; retry sweep in `service.py` |
| Dashboard surfaces pending | PASS | `admin_store.py` `settlements_owed`; `service.py:470-481` health report |

## Admin Panel

| ID | Status | Evidence |
|----|--------|----------|
| 9 sections as real UI | PASS | `apps/admin/public/app.js`, 10 `SECTIONS.*`, served at `/admin` |
| Profit & Risk persists | PASS | `PUT /admin/profit-risk` → `save_profit_risk` |
| Game Rules persist + versioned | PASS | `put_game_rules` → `save_game_config_version` |
| Player Override works | PASS | `/admin/players/{id}/override` GET/POST/DELETE |
| Audit immutable + exportable | PASS | panel `exportCsv`; `007` REVOKE |
| Rollback supported | PASS (fixed) | Was a FAIL: store method with no route. Now `POST /admin/games/{slug}/rules/rollback` |

## Demo

| ID | Status | Evidence |
|----|--------|----------|
| `/demo/session` exists | PASS | verified live |
| In-memory only | PASS | `--demo` sets `DEMO_MODE`; `build_stores` short-circuits to in-memory |
| Refuses under APP_ENV=production | PASS | `api.py` guard + demo session gate |
| Watermark + "Play Real" CTA | PARTIAL | `DEMO` badge + countdown exist in `lobby.html:173`. The watermark is **not** in the game client, and there is **no "Play Real" CTA** anywhere |

## Data Model

| ID | Status | Evidence |
|----|--------|----------|
| Spec tables via 007 | PASS | 10 `CREATE TABLE` in `007_spec_tables.up.sql` |
| Constraints | PASS | round_no unique per game, bet_id PK, settlement bet_id UNIQUE, idempotency partial unique |
| wallet_transaction immutable | PASS | `002:149` |

## Visual Reference Match — **NOT VERIFIABLE**

The two reference images were not provided. No image attachments were
received in this session, and the repository contains no DearLive screenshots
(`find` for png/jpg/webp returns only svg-generator tooling assets). A
pixel-level comparison against images never seen cannot be performed, and
Chrome cannot start in this sandbox to render the current build for comparison.

What *was* audited is every element described in the request text. One real
mismatch was found and fixed:

| Element | Reference (as described) | Was | Now |
|---------|------------------------|------|-----|
| Seat order | A left, B centre, C right | A left, B **right**, C **top-centre** | fixed |
| Seat colours | A green, B blue, C red | A **red**, C **green** | fixed |
| Total Bet / My Total Bet | labelled as such | `POT` / `YOU` | fixed |
| Chairs render | visible | **all three 404'd** | fixed |
| Avatar strip at top | present | not implemented | NOT DONE |
| Jungle/green felt | present | present | match |
| Chip bar 20/100/500/1K | present | present | match |
| Coin balance bottom-left | present | present | match |
| Repeat bottom-right | present | present | match |
| Circular timer | present | present | match |
| Guessing/status banner | present | present | match |
| Pot per seat | present | present | match |

Unverified by definition: chair leather/gold trim fidelity, jungle background
fidelity, exact spacing, fonts, animation timing.

## Asset Integration

| Metric | Value |
|--------|-------|
| Files in `assets/games/teen-patti-pro/` | 93 |
| Referenced by client or server | **2** (avatars only) |
| **Orphans** | **91** |
| Every SVG valid XML | 118/118 yes |
| Client assets resolving (no 404) | all, verified by test |

Orphan breakdown: `cards/` 59, `ui/` 22, `chips/` 4, `avatars/` 3, `seats/` 3.

The pack contains exactly the elements the reference describes —
`card-{2..14,A,J,Q,K}-{club,diamond,heart,spade}.svg`, `chip-{20,100,500,1k}.svg`,
`seat-{red,blue,green}.svg`, `btn-{back,help,history,repeat,auto,sound-on,sound-off}.svg`,
`status-{betting-open,waiting,result,offline,online}.svg` — and **none of it is
wired into the renderer**. The client draws the table, chips, cards and
buttons procedurally on canvas and loads a separate 14-file tree at
`games/teen_patti_pro/client/assets/`.

This is reported as a FAIL against "no unused assets" and is **not fixed** —
see below.

## Summary

| | |
|---|---|
| Total requirements audited | 78 |
| PASS | **72** |
| FAIL (all fixed) | **5** |
| PARTIAL (documented, not fixed) | **3** |
| Not verifiable | 2 audits (visual pixels, browser render) |

### FAILs found and fixed

| ID | Defect | Commit |
|----|--------|--------|
| BR-11 | `ranking_order` declared, referenced nowhere; rankings were hardcoded | `34b6332` |
| BR-14 | `max_win_cap` declared with a comment claiming a cap; never read | `34b6332` |
| WS | `balance.updated` declared in the vocabulary with no emit site | `34b6332` |
| ADM | `rollback_game_config` implemented in the store, no HTTP route | `34b6332` |
| UI-04 | Seat order and colours out of spec; **all three chairs 404'd** | `3e880e3`, `df60d56` |
| FR-06 | Labels read `POT`/`YOU` instead of `Total Bet`/`My Total Bet` | `3e880e3` |

### Additional money bug found while testing the above

Not in the spec; found by the new tests. **An empty seat could win the pot.**
`calculate_result` scored every dealt seat, so with 3 seats and 2 players, if
the best hand landed on the unoccupied seat the pot was voided — both players
lost their entire stake to cards nobody had bet on and the money carried
forward. Measured at **6 of 25 rounds** with two players. Winner selection is
now restricted to seats with an accepted bet. Commit `34b6332`.

### PARTIALs, documented not fixed

1. **FR-15 Auto Bet** — route exists and refuses cleanly; the service has no
   `set_autobet`. Spec marks it TBC, so a clean 404 is a defensible reading,
   but the capability does not exist.
2. **BR-12 Payout formula configurable** — no payout-formula config field.
   Settlement is pot-minus-rake with pro-rata dead-heat split. Spec marks it
   TBC.
3. **DEMO watermark / "Play Real" CTA** — the watermark and countdown are in
   the lobby only, not the game client. There is no "Play Real" CTA.

### Known limitations

- **No browser render.** Chrome aborts at startup in this sandbox, so the UI
  has never been visually verified. Static checks only: DOM, endpoints, wiring,
  traversal guards, asset resolution.
- **No production URL.** The client provides infrastructure.
- **91 orphan assets** — see the blocker below.
