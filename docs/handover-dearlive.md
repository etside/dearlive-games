# Handover: load Teen Patti Pro into the DearLive Games section

For the DearLive app team. All three delivery plugins are live
(TBC-gated on money paths until business confirms rules):

| # | Catalog (`GET /api/v1/games`) | Engine |
|---|---|---|
| 1 | Teen Patti Pro (`teen-patti-pro`, alias `teen_patti`) | `games/teen_patti_pro/` |
| 2 | Greedy Lion (`greedy-lion`, alias `greedy_lion`) | `games/greedy_lion/` outcome + generic `WheelService` |
| 3 | Monkey Wheel (`monkey-wheel`, alias `monkey_wheel` → `greedy-monkey` engine) | `games/greedy/` outcome + generic `WheelService` |

Legacy IDs (`greedy-monkey`, `baby-king`, `food-wheel`, …) keep resolving
as aliases so existing tokens/links keep working. Per-game asset manifests:
`GET /api/v1/games/{gameId}/assets`. No host-app layout change is needed: the game is a static
WebView client + a Python game server that plugs into DearLive's existing
`GAMES_BASE_URL` + Redis launch-token + wallet pattern (proven by your
`DearLive.apk` `.env`: `GAMES_BASE_URL=https://games.dearlive.pro`).

## What you receive

| Item | Location |
|---|---|
| Playable WebView client (no build step) | `games/teen_patti_pro/client/` (`index.html`, `game.js`, `theme.json`, `assets.json`, `assets/*.svg`, `demo.html` offline replay) |
| Game server (stdlib Python only) | `games/teen_patti_pro/api.py` (REST `:5002`), `ws.py` (`:5003`), `service.py`, `engine.py` |
| Catalog/config for tiles | `GET /api/v1/games`, `GET /api/v1/games/teen-patti-pro` |
| Protocol specs | `docs/INTEGRATION.md`, `api/openapi.yaml`, `docs/postman_collection.json`, `docs/realtime.md`, `docs/webhooks.md`, `docs/IDEMPOTENCY.md`, `docs/wallet-integration.md`, `docs/launch-integration.md` |
| SDKs + example | `sdk/javascript-client.js`, `sdk/python_client.py`, `tools/integration_example.py` |
| Brand tokens | `client/theme.json` (`palette` incl. DearLive gold/rose, `theme`, live `canvas` skin) |

## Load it in 5 steps

### 1. Serve the static client (pick one)
- **Dev:** `python3 -m games.teen_patti_pro.api --port 5002 --confirmed &`
  then open `http://127.0.0.1:5002/teen-patti-pro/?session=<id>&room=default`.
- **Prod:** copy `games/teen_patti_pro/client/` to your games CDN/origin as
  `https://games.<your-domain>/teen-patti-pro/` (same relative layout —
  `game.js` fetches sibling `theme.json`). No build, no npm.

### 2. Add the Games-section tile (no layout change)
Render from `GET {GAMES_BASE_URL}/api/v1/games` (`entry:"/teen-patti-pro/"`,
`dearlive_code:"teen_patti"`). Tile URL:
`{GAMES_BASE_URL}/teen-patti-pro/?session=<session_id>&room=<room_id>`.
Optional overrides (static assets unchanged): `?api=<engine>&ws=<ws>`.

### 3. Mint launch tokens in the SAME Redis the game server reads
Key `{TOKEN_KEY_PREFIX}{token}` (default `dearlive:launch:`), JSON
`{player_id, room_id, game_id, expires_at_ms, nonce}`, TTL `TOKEN_TTL_MS`
(default 120s). Format `dl_<game_id>_<random>`, single-use. Wrong Redis ⇒
the client fails exactly like your vendor note ("connection error 10001"
class). Client redeems: `POST /api/v1/sessions {launch_token}` →
`{session_id, player_id, room_id, game_id}` (atomic GET+DEL), then
`Authorization: Bearer <session_id>` on every call.

### 4. Map the wallet (you own all balances)
`WALLET_BASE_URL` (falls back to `DEARLIVE_API_BASE_URL`), integer minor
units (`COIN_CURRENCY`). Endpoints the game calls: `GET
/wallets/{player}/balance`, `POST /wallets/debit {player_id, amount,
ref:"bet:{room}:{idem}", idempotency_key}`, `POST /wallets/credit`
(`ref:"settle:{bet_id}"`), `POST /wallets/refund`, `GET
/wallets/txn/{idempotency_key}` (**mandatory before any money retry**).
Game-side records only (`db/schema.sql`) — never balances.

### 5. Operate it
- Round control: `POST .../rooms/{room}/rounds/{start|close|result|settle}`
  (or `service.sweep()` 1s loop); admin via `X-Admin-Key`
  (`GAME_ADMIN_KEYS=key:role,...`).
- Realtime: WS `:5003` today, Socket.IO-compatible events (map in
  `docs/realtime.md`); countdowns use server `serverTime` only.
- Settlement webhooks: HMAC-SHA256, retries → DLQ (`docs/webhooks.md`).
- Money is gated by `confirmed`: staging runs TBC rules visibly fenced
  (`403 TBC_RULE_UNCONFIRMED` on money paths until business confirms).

## Brand / UI verification (done, this handover)

Checked against `DearLive.apk` (gold/pink-romance brand, cyan-gold games
icon) and the BRD Games 1–3 (G3: A/B/C seats, cards, Guessing timer, pots,
chips 20/100/500/1K, Repeat, balance, Back/Help/Sound/Menu, round #,
connection, history). Results:
- Canvas client renders the full BRD G3 control set; denoms match exactly.
- `theme.json:canvas` is now the live skin (`game.js` fetches it at boot,
  compiled defaults = current look) — re-skin without touching logic.
- `client/assets/*.svg` re-skinned to the same casino-red/gold language
  (was navy-tech); chips/glow were already casino-gold, kept.
- Avatars (BRD "Should"): seats render position badges + pots; player
  display names flow through when included in launch-token/session payloads
  (optional extension, non-blocking).

## Before you call it production

1. Confirm the TBC list (`docs/business-tbc.md`, BRD §14): Teen Patti
   variant/ranking, pot/payout + tie rule, timer durations, denoms/min/max,
   result-generation sign-off (`confirmed` flag).
2. Fill `.env.example` → staging secrets; `APP_ENV=production` refuses to
   boot when anything is missing.
3. Run UAT from `docs/UAT.md` (BRD §13 matrix) with a funded test wallet +
   launch token (`tests/test_api.py` shows the pattern); E2E conservation
   proof: `tests/test_e2e_dearlive_flow.py` (76 tests green).
