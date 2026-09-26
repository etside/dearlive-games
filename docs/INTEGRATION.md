# DearLive ↔ Games Integration Contract (v1)

DearLive (host app `xyz.lrlive.app`, Games section) owns **users, rooms
membership, and wallet balances**. The game server owns **deterministic game
logic + round records** and never keeps a production money balance. All money
moves through DearLive wallet APIs; all entry goes through single-use launch
tokens in the **same Redis** both sides share.

Environments are separate: `APP_ENV=sandbox|staging|production`. The DearLive
developer configures their own real addresses/credentials/currency via env
(`.env.example`) — **no game-logic changes, no hardcoded hosts/secrets.**

## 1. Game catalog (Games section layout preserved)

The existing DearLive Games-section visual structure is untouched. The host
renders game tiles from the catalog; the game source supplies metadata only.

- `GET {GAMES_BASE_URL}/api/v1/games` → `{success:true, data:[{game_id,
  name, version, status:live|planned, entry:"/teen-patti-pro/", config_version}]}`
- `GET {GAMES_BASE_URL}/api/v1/games/teen-patti-pro` → seats, denoms,
  min/max bet, guess_ms, tbc list, confirmed, config_version.
- Adding a game = DearLive developer adds a tile pointing at
  `{GAMES_BASE_URL}/<game_id>/?session=<session_id>&room=<room_id>`. No host
  layout change.

## 2. Launch / session / identity

1. DearLive backend mints a launch token into shared Redis:
   key `{TOKEN_KEY_PREFIX}{token}` (`TOKEN_KEY_PREFIX`, default
   `dearlive:launch:`), value JSON
   `{player_id, room_id, game_id, expires_at_ms, nonce}`, TTL `TOKEN_TTL_MS`
   (default 120_000 ms). Token format `dl_<game_id>_<random>`, single-use.
2. Host opens WebView: `{GAMES_BASE_URL}/teen-patti-pro/?session=&room=`
   (dev: `http://127.0.0.1:5002/teen-patti-pro/`; prod: developer's
   `GAMES_BASE_URL`, e.g. `https://games.<domain>`). Client overrides via
   `?api=&ws=` — static assets unchanged.
3. Client calls `POST {GAMES_BASE_URL}/api/v1/sessions {launch_token}` →
   `{session_id, player_id, room_id, game_id}`. Redeem is atomic GET+DEL;
   replay/expired/wrong-game → `401 INVALID_TOKEN` / `422 VALIDATION_ERROR`.
4. Player calls carry `Authorization: Bearer <session_id>`. Sessions live in
   Redis (`dearlive:session:`) in prod, memory only in sandbox.
5. Identity is DearLive's: `PLAYER_PROFILE/PLAYER_STATUS/PLAYER_BALANCE`
   reads hit DearLive APIs (`integrations/dearlive.py:DearLiveIdentityClient`).
   Banned/suspended players are rejected at session open. Room creation sync:
   DearLive creates rooms; game auto-creates the room record on first join
   (same `room_id` string).

## 3. Base URLs / configuration (developer-owned)

| Item | Env | Example shape (developer fills) |
|---|---|---|
| Games base URL | `GAMES_BASE_URL` | `https://games.<dearlive-domain>` |
| DearLive API | `DEARLIVE_API_BASE_URL` | `https://api.<dearlive-domain>/api/v1` |
| Wallet API | `WALLET_BASE_URL` (falls back to DearLive API) | same host, `/wallets/...` |
| Redis shared | `REDIS_HOST/PORT/DB/USERNAME/PASSWORD/TLS`, `TOKEN_KEY_PREFIX` | least-privilege staging creds |
| Currency | `COIN_CURRENCY` (default `COIN`) | integer minor units everywhere |
| Webhooks | `SETTLEMENT_WEBHOOK_URL`, `SETTLEMENT_SIGNING_SECRET` | DearLive receiver URL |
| Game DB | `DATABASE_URL` | game records only (no balances) |
| Admin | `GAME_ADMIN_KEYS=key:role,...` | roles `admin|superadmin` |

Validate prod: `Settings.validate_for_production()` (all above required).

## 4. Wallet: balance / debit / credit

- `GET_BALANCE GET {wallet}/wallets/{player_id}/balance` →
  `{available:int, currency}`. Game calls before bet display; the debit is
  the atomic guard (balance-check → debit race is resolved by the adapter).
- `DEBIT_BET POST {wallet}/wallets/debit {player_id, amount:int,
  ref:"bet:{room}:{idem_key}", idempotency_key:"bet:{key}", currency}` →
  `{txn_id}`. Raises `INSUFFICIENT_BALANCE` (mapped to `402`) when short.
- `CREDIT_WIN POST {wallet}/wallets/credit {player_id, amount:int,
  ref:"settle:{bet_id}", idempotency_key:"settle:{bet_id}", currency}`.
- `REFUND POST {wallet}/wallets/refund` — same shape; used for void/cancel
  (`ref:"cancel:{bet_id}"`) and post-debit failures
  (`ref:"bet-void:{room}:{key}"`).
- `GET_TRANSACTION_STATUS GET {wallet}/wallets/txn/{idempotency_key}` —
  **mandatory before any retry after a timeout/transport error**. Never
  blind-retry a money write.
- Bet order enforced by the service: auth → game → round → action → amount →
  min/max → balance → window → idempotency-claim → atomic debit → create bet
  → publish. Amounts must be configured denoms within min/max.

## 5. Entry / join / bet / action

- Join = `POST /api/v1/sessions` (token redeem) + first authenticated state
  fetch `GET /api/v1/games/teen-patti-pro/rounds/current?room=`.
- Bet = `POST /api/v1/games/teen-patti-pro/rooms/{room}/bets
  {position:A|B|C, amount:int}` with `Idempotency-Key` header REQUIRED.
  Same key + same payload → exact replay (no second debit). Same key +
  different payload → `409 DUPLICATE_REQUEST`. Missing key → `422`.
- Post-debit invariant: ANY failure creating the bet after a successful debit
  triggers an automatic compensating credit (stake refunded, never lost).
  Late/betting-closed race → `409 BETTING_CLOSED` + refund.

## 6. Settlement / rollback / refund

- Operator/scheduler: `POST .../rooms/{room}/rounds/close` →
  `POST .../rounds/result` → `POST .../rounds/settle`, or `service.sweep()`
  every second (close → result → settle, idempotent, per-room reports).
- Settlement rows are deterministic; one bet = one settlement
  (`UNIQUE settlement.bet_id`, `UNIQUE(bet_id)` in Postgres). Re-settle replays
  identical rows, credits once (`settle:{bet_id}` idempotency + settle lock).
- Winners split pro-rata by stake (dead-heat); floor dust → `carry_out`
  (no seat bias); no winnable bets → pot carries forward. Rake `rake_bps`.
- Cancel (pre-RESULT only): `service.cancel_round()` voids bets + issues
  `cancel:{bet_id}` credits. Post-RESULT cancel is rejected (`STATE_CONFLICT`).
- Every settlement stamps `config_version`; `confirmed=False` blocks all money
  paths (`403 TBC_RULE_UNCONFIRMED`).

## 7. Realtime (WebSocket today, Socket.IO-compatible events)

Transport today is stdlib WS (`ws.py`, port 5003); events are
transport-agnostic and map 1:1 to Socket.IO. Full map: `docs/realtime.md`.

- Subscribe: `{type:"subscribe", session, room}` → `snapshot` + `subscribed`.
- Server → client: `snapshot | event | tick | error | pong`.
- Event kinds: `round.started, bet.accepted, bet.rejected, betting.closed,
  result.published, settlement.completed, session.completed, round.cancelled`.
- `tick` every 1s `{round.tick, serverTime, betting_end_at}` — clients use
  `serverTime` only (client clocks untrusted).
- Reconnect: `POST .../rooms/{room}/reconnect {session_id, last_seen_seq}` →
  `{snapshot, missed_events}` (hands redacted pre-RESULT, capped at
  `event_log_cap`). Socket.IO mapping: namespace `/games`, room =
  Socket.IO room, `snapshot`/`event`/`tick` = Socket.IO events with acks;
  `subscribe` = `join`, missed-seq replay = `resync`.

## 8. Authentication

- Players: `Authorization: Bearer <session_id>` (issued by token redeem).
  Unknown/missing → `401 UNAUTHENTICATED`.
- Admin/operator: `X-Admin-Key: <key>` mapped to roles via `GAME_ADMIN_KEYS`
  (`admin`, `superadmin`) + RBAC matrix (`admin/api.py`: config, rounds,
  rooms, players/bets/results/settlements/audit view, webhooks, maintenance).
  No game action can force wins/losses.
- DearLive upstream calls: `X-Api-Key` + `X-Client-Id`, Bearer client secret
  (or HMAC/mTLS as DearLive specifies via `DEARLIVE_AUTH_TYPE`). Secrets via
  env/secrets-manager only — never in git.

## 9. Webhooks + signatures

Full spec: `docs/webhooks.md`. Game → DearLive `POST {SETTLEMENT_WEBHOOK_URL}`
events (`game.session.created, player.joined/left, round.started,
bet.accepted/rejected, betting.closed, result.published,
settlement.completed, session.completed, round.cancelled, error`).
Headers `X-Webhook-Event/Delivery/Timestamp/Signature`, signature
`hex(HMAC-SHA256(signing_secret, raw_body))`, retry 1s→5s→30s→5min then DLQ;
receivers verify signature first and de-duplicate on `event_id`.

## 10. Idempotency / errors / ledger consistency

- Rules: `docs/IDEMPOTENCY.md`. Every money write carries
  `Idempotency-Key`; Redis `SET NX EX` in prod (memory in sandbox).
  In-flight key replay returns conflict-safe retry; completed key replays the
  stored result.
- Error codes (handle by `code`, never message): `UNAUTHENTICATED 401,
  FORBIDDEN 403, NOT_FOUND 404, VALIDATION_ERROR 422, BETTING_CLOSED 409,
  INSUFFICIENT_BALANCE 402, DUPLICATE_REQUEST 409, STATE_CONFLICT 409,
  RATE_LIMITED 429, TBC_RULE_UNCONFIRMED 403, INTERNAL_ERROR 500`.
- Ledger consistency: DearLive ledger is append-only; game-side
  `db/schema.sql` mirrors `{bet_id → debit_txn_id, payout → credit_txn_id}`.
  Conservation check per round: `sum(debits) == distributed + carry_out`
  (rake-adjusted); E2E test `tests/test_e2e_dearlive_flow.py` asserts it.
