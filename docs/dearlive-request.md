# DearLive Team — Integration Credentials & Specifications Request
**Project:** Teen Patti Pro (Game 1) · **Date:** 2026-09-21 · **Scope:** STAGING/TEST ONLY

> The game module is built and locally verified (29 tests). To connect it to
> DearLive we need ONLY the items below. Do NOT send production secrets,
> Super Admin credentials, JEV internals, or unrestricted DB/Redis access.

## A. DearLive API (staging)
```
DEARLIVE_API_BASE_URL=
AUTH_TYPE=            # e.g. Bearer / HMAC / mTLS (only fields DearLive uses)
TOKEN_TYPE=
CLIENT_ID=
CLIENT_SECRET=
API_KEY=
```

## B. Launch token (WebView game entry)
```
LAUNCH_TOKEN_MINT_ENDPOINT=
```
Specify: HTTP method · auth · request body · response format · TTL · claims ·
player-ID mapping · room-ID mapping · game-ID mapping · single-use vs reusable.

## C. Redis launch-token contract (staging, least-privilege preferred)
```
REDIS_HOST=  REDIS_PORT=  REDIS_DB=  REDIS_USERNAME=  REDIS_PASSWORD=  REDIS_TLS=
TOKEN_KEY_PREFIX=  TOKEN_SCHEMA=  TOKEN_TTL=  TOKEN_CONSUMPTION_RULE=
```
Preferred: dedicated staging credentials limited to the game token namespace.

## D. Wallet API (staging) — game NEVER touches DearLive's wallet DB directly
For each of `GET_BALANCE, DEBIT_BET, CREDIT_WIN, REFUND, GET_TRANSACTION_STATUS`
provide: endpoint · method · auth · request/response/error schemas · currency ·
decimal/rounding rules · transaction-ID format · idempotency rules ·
timeout/retry rules · signature requirements.

## E. Wallet security (staging-only, rotatable, least-privilege)
```
WALLET_API_KEY=  WALLET_CLIENT_ID=  WALLET_CLIENT_SECRET=
WEBHOOK_SECRET=  SIGNING_METHOD=
```

## F. Settlement webhook
```
SETTLEMENT_WEBHOOK_URL=  SETTLEMENT_SIGNING_SECRET=
```
Document: event types · signature method · payload · retry policy ·
duplicate-event behavior · settlement status lifecycle.

## G. Player / room contract (staging APIs/events)
`PLAYER_ID, PLAYER_PROFILE, PLAYER_STATUS, ROOM_ID, ROOM_STATUS, ROOM_PLAYERS,
PLAYER_BALANCE` — plus: who creates rooms (DearLive / game server / both + sync rule).

## H. Games base URL + WebView (staging)
```
GAMES_BASE_URL=
```
Plus: allowed origin · viewport · orientation · safe-area · cookie/storage needs ·
deep links (if any). Expected pattern: `GAMES_BASE_URL/teen-patti-pro/` + token.

## I. Admin / RBAC (staging test account, minimum permissions)
Map DearLive permissions for: game config · player/room/game monitoring ·
bet+result viewing · settlement viewing · win/loss reports · audit logs.
No Super Admin credentials.

## J. Environment rules
- Staging/test credentials ONLY. Secrets via env/secrets-manager
  (`.env.example` / `.env.local` / `.env.staging`); git must never contain them.
- Specs still required (not credentials): items 1–14 of the handover brief
  (token API spec, Redis schema, wallet spec, webhook spec, ID mappings, currency,
  limits, rounding, game ID/name, WebView reqs, RBAC map, prod deploy reqs, staging info).

## K. When you deliver → our order
Launch Token → Redis validation → Player/Room → Wallet debit → Bet flow →
Result → Wallet credit → Settlement webhook → full `docs/uat.md` matrix.
Staging UAT must pass before any production credential is discussed.
