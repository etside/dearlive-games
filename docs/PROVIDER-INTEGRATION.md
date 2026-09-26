# Teen Patti Pro — B2B Provider API integration

One API connection and one API key per operator. DearLive/Uradhura is the first
client; any aggregator can reuse the same contract.

```
Operator backend
   │  X-API-Key + HMAC-SHA256
   ▼
POST /api/v1/sessions  ──► session_id, session_token (gst_...), launch_url
   │
   ▼  player WebView / APK (session token only — never the secret)
Teen Patti frontend ──WebSocket {session_token}──► authoritative engine
   │
   ▼
Debit → engine decides → settlement → credit  (operator wallet = money of record)
```

The engine stays authoritative for cards, pots, timers, winners and settlement.
The provider keeps an append-only transaction ledger. No balance, winner, bet
amount or result is ever taken from the client.

## 1. Authentication

Every server-to-server call carries four headers:

| Header | Value |
|---|---|
| `X-API-Key` | public key id (e.g. `tp_live_xxx`) |
| `X-Timestamp` | unix seconds |
| `X-Nonce` | unique per request, single use |
| `X-Signature` | lowercase hex `HMAC-SHA256(secret, canonical)` |

```
canonical = METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256_HEX(raw_body)
```

* `PATH` is the path **without** the query string.
* `SHA256_HEX` is over the exact bytes sent as the body.
* Timestamps outside a 300 s window are rejected; a reused nonce is rejected.
* The secret is configured server-side (`PROVIDER_API_KEYS=key_id:secret`) and is
  never returned by the API, logged, or placed in a player app.

## 2. Try it with curl (no dependencies)

`tools/provider_sign.py` prints a ready-to-run command:

```bash
export PROVIDER_BASE_URL=http://127.0.0.1:5002
export PROVIDER_API_KEY=tp_live_demo
export PROVIDER_API_SECRET='your-shared-secret'

eval "$(python3 tools/provider_sign.py --raw GET /api/v1/provider/health)" 2>/dev/null
python3 tools/provider_sign.py GET /api/v1/games
python3 tools/provider_sign.py POST /api/v1/sessions \
  --body '{"player_id":"player_10025","game_code":"teen_patti_pro","currency":"COIN","language":"en","platform":"android","return_url":"https://dearlive.example.com/games"}'
python3 tools/provider_sign.py POST /api/v1/wallet/debit \
  --idempotency-key round_1_bet_1 \
  --body '{"player_id":"player_10025","amount":200,"currency":"COIN","game_code":"teen_patti_pro","round_id":"round_1","reference":"round_1_bet_1"}'
```

Or sign by hand in any language — the reference implementation is
`provider/auth.py:sign_request` (also mirrored in `sdk/python_client.py` and
`sdk/javascript-client.js`).

### Postman

Import `docs/postman_collection.json`. The collection has a pre-request script
that fills `X-Timestamp`, `X-Nonce` and `X-Signature` from the `providerSecret`
variable, so every request signs itself.

## 3. Happy path

| Step | Call |
|---|---|
| health | `GET /api/v1/provider/health` (unauthenticated liveness) |
| catalog | `GET /api/v1/games` |
| launch | `POST /api/v1/sessions` → `session_id`, `session_token`, `launch_url` |
| open game | WebView opens `launch_url` (redirects to the client with the token) |
| table | `GET /api/v1/teen-patti/tables`, `GET .../tables/{tableId}` |
| seat | `POST /api/v1/teen-patti/tables/{tableId}/join` |
| play | `POST .../action` (bet) + WebSocket push |
| state | `GET .../state`, `GET .../history` |
| money | `POST /api/v1/wallet/debit`, `/credit`, `/rollback`, `GET .../transactions/{playerId}` |
| end | `DELETE /api/v1/sessions/{sessionId}` |

`POST /api/v1/sessions` also performs matchmaking: pass `table_id` to choose a
table, or `amount` to have the provider pick one that accepts the stake. It
returns the chosen `table_id`.

## 4. Wallet and money rules

* Every write requires `Idempotency-Key` and a unique `reference`.
* A repeated key returns the **original** transaction with `replayed: true`; it
  never moves money twice. Reusing a key with a different payload is rejected
  `409 DUPLICATE_REQUEST`.
* `rollback` appends a compensating credit bound to `original_reference`; the
  original row is never edited or deleted, and a second rollback of the same
  reference is rejected.
* Amounts are integers in minor units. `currency` must match the configured
  currency.
* Money of record is the operator's (DearLive) wallet via
  `integrations/dearlive.py`. Staging keeps TEST coins in Redis and the faucet is
  refused in production. There is no provider-side balance in production.

## 5. Realtime

Connect to `/ws/game` and send the session token:

```json
{"session_token": "gst_..."}
```

The server takes the room from the authenticated session, never from the
client. Frames are `{"kind":"snapshot"|"event"|"error"|"pong","data":{...}}`.
`round.tick` arrives once per second while a round is open.

| Engine event | Provider event |
|---|---|
| `round.started` | `game.started` |
| `bet.accepted` | `bet.placed` |
| `betting.closed` | `betting.closed` |
| `result.published` | `game.finished` |
| `settlement.completed` | `round.settled` |
| `player.joined` / `player.left` | same |
| errors | `game.error` |

## 6. V1 scope (important)

The V1 provider wraps the **existing** Teen Patti Pro engine unchanged. That
engine runs one betting phase over positions A/B/C: there are no player turns
and no fold/show actions. Therefore:

* `POST .../action` accepts `action: "bet"` only. `fold` and `show` return `422`
  with an explicit message rather than being simulated.
* `turn.started`, `player.folded` and `show.requested` are documented as not
  emitted (see `x-provider.websocket.v1_not_emitted` in the OpenAPI document).
  They are not faked; adding them means a rules change and a new engine version.
* Game rules, ranking, pots, rake and settlement are unchanged. Rounds settle
  from the server clock: the API process sweeps expired betting windows every
  second, and the serverless deployment sweeps lazily per request.

If the business wants the turn/fold/show model, that is a separate engine
version behind a new `game_code`, not a change to this contract.

## 7. Configuration reference

See `.env.example` for every variable. The provider-specific ones:

| Variable | Default | Meaning |
|---|---|---|
| `PROVIDER_API_KEYS` | — | `key_id:secret,...`; no keys = provider auth unavailable |
| `PROVIDER_PUBLIC_BASE_URL` | — | origin used to build `launch_url` |
| `PROVIDER_CLIENT_PATH` | `/teen-patti-pro/?session=` | launch redirect target |
| `PROVIDER_TABLES` | 3 tables | `id:label:min:max:seats:CURRENCY` |
| `PROVIDER_CURRENCY` | `COIN` | accepted currency |
| `PROVIDER_SESSION_TTL_SECONDS` | `1800` | session token lifetime |
| `PROVIDER_RATE_LIMIT` / `_WINDOW_SECONDS` | `600` / `60` | per-key fixed window |
| `PROVIDER_IDEMPOTENCY_TTL_SECONDS` | `86400` | idempotency retention |
| `PROVIDER_LEDGER_CAP` | `0` | `0` keeps every ledger row (immutable) |

## 8. Security notes

* Provider routes are HMAC-signed; shared paths (`/api/v1/games`,
  `/api/v1/sessions`) fall back to their legacy behaviour when no provider
  context is configured.
* `GET /api/v1/sessions/{id}` is operator-readable; a player may read only
  their own session with their own bearer token.
* Rate limiting is per API key. Bodies over 64 KiB are refused.
* Privileged calls are audited with actor, action, entity and payload.
* `PROVIDER_API_KEYS` belongs in a secret manager. Rotate by replacing the env
  value and redeploying; rotation instructions are in
  `docs/staging-credentials.md`.

## 9. Reference

* OpenAPI 3.1: `api/openapi.yaml` (generated from `provider/spec.py`)
* Runtime: `GET /openapi.json` and `GET /docs` on any deployment
* Tests: `python3 -m unittest tests.test_provider_api`
