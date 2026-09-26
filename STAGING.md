# STAGING (Vercel, no VPS)

> "This environment is for UAT/game validation. Authentication and wallet
> are staging adapters. The game engines, API contracts, round lifecycle,
> betting, result and settlement logic are the implementation intended for
> DearLive integration."

## URL

`https://dearlive-games-staging-6fejccerl-whotjms-projects.vercel.app`
(redeploys get fresh preview URLs; `vercel ls` lists them).

## Login (staging adapter — any player name works, no password)

1. Open `/` → pick player + room → **Login + 20K test coins**.
2. `POST /api/v1/staging/test-login {player, room, game}` → `{launch_token}`.
3. `POST /api/v1/sessions {launch_token}` → `{session_id}` (Bearer below).
4. Grant more: `POST /api/v1/staging/test-wallet/grant
   {player, amount, idempotency_key}` → `{txn_id, available, TEST}`.
5. Open a game card → plays with `?session=&room=` (same-origin `/api`).

Test accounts: `qa-player`, `qa-player-2`, `qa-player-3` (any name works;
first login funds 20,000 TEST coins, idempotent per player).

## Admin (X-Admin-Key header; roles admin > operator > auditor)

Keys are issued per-deployment and stored ONLY as Vercel env secrets
(`GAME_ADMIN_KEYS`) — never in git. See `docs/staging-credentials.md`
for roles, rotation, and who holds the current values.
- auditor: read admin endpoints (config views, games, audit, webhooks).
- operator: + round start/close/result/settle.
- admin: general operator role.
- admin: + `PUT /api/v1/admin/games/{id}/config` (audited with
  before/reason/updated_by/applied_at).

## Games

Teen Patti Pro (`/teen-patti-pro/`), Greedy Lion (`/greedy-lion/`),
Monkey Wheel (`/monkey-wheel/`). All three: round → bet → close → result →
settle → history → reconnect, wallet debited/credited in TEST coins,
idempotent bets/settlements, audited admin ops.

## Reset test data

Flush the staging Redis DB (Upstash dashboard → Data → Flush, or
`redis-cli -h $REDIS_HOST -a $REDIS_PASSWORD -n $REDIS_DB FLUSHDB`).
Rooms, sessions, wallets, idempotency, audit spill are all Redis-backed.

## Known limitations (staging transport differences)

- **Polling, not WebSockets.** Vercel serverless cannot host the raw-socket
  WS server (`ws.py` preserved for production). Clients poll
  `rounds/current` every 2s (`● POLLING` indicator); same authoritative data.
- **Lazy sweep, not a ticker.** Expired betting windows close→result→settle
  on the next request touching the game (idempotent service sweeps);
  worst-case delay equals request gap, not wall-clock.
- **No Postgres mirror** (`db/schema.sql` is a records contract for the
  DearLive side; staging history comes from Redis-capped room/audit data).
- **TEST coins are valueless.** Faucet refuses `APP_ENV=production`;
  currency field reads `TEST`.

## Identical to final DearLive integration

Engines, round lifecycle, bet validation order, idempotency keys, HMAC
result generation, settlement math (incl. dead-heat/tie handling),
conservation checks, envelope + error codes, RBAC roles, asset manifests,
realtime event names/shapes (delivered via polling payload + tick).

## Staging-only (NOT production)

`/api/v1/staging/*` routes, Redis wallet + faucet, test-login, welcome
funding, lazy sweep timing, polling transport, `confirmed=True` sandbox
config (staging coins have no value; production keeps TBC gating).
