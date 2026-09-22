# REST API v1 — all games (envelope on everything)

Envelope: `{success, code, message, data, serverTime, requestId}` (serverTime authoritative).
Auth: player `Authorization: Bearer <session_id>` (any game's session accepted
cross-game); admin `X-Admin-Key` (+ RBAC matrix; config PUT = superadmin only).
Bets: `Idempotency-Key` header (or `idempotencyKey` body field for tables clients).
Lifecycle: `UPCOMING → BETTING_OPEN → BETTING_CLOSED → RESULT_PROCESSING →
RESULT → SETTLED → CLOSED`. Full contract: `docs/integration-contract.md`,
machine spec: `docs/openapi.yaml`, traceability: `docs/traceability.md`.

## Cross-game routes (every game; `{gameId}` incl. aliases like `teen_patti`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/games` | catalog (game_id, name, status, entry, dearlive_code) |
| POST | `/api/v1/games/{gameId}/sessions` `{launch_token}` | join (game-scoped token) |
| GET | `/api/v1/games/{gameId}/rounds/current?room=` | state: timer, options/totals or pots, my contribution |
| POST | `/api/v1/games/{gameId}/rounds/{roundId}/bets` | bet (position \| option_id, amount; stale roundId → 409) |
| GET | `/api/v1/games/{gameId}/rounds/{roundId}/result?room=` | authoritative result (404 pre-RESULT) |
| GET | `/api/v1/games/{gameId}/history?room=` | own bets (+ earnings_today on wheels) |
| GET | `/api/v1/games/{gameId}/results/recent?room=` | recent-result strip (wheel games) |
| POST | `/api/v1/games/{gameId}/autobet` \| `/autoplay` | Auto Bet/Play config (403 where unapproved) |
| POST | `/api/v1/games/{gameId}/tables/{tableId}/bets` | G3 tables bet (roundId/position/amount/idempotencyKey) |
| GET | `/api/v1/games/{gameId}/tables/{tableId}/state` | G3 table state (players, pots, timer, card visibility) |
| POST | `/api/v1/games/{gameId}/rooms/{room}/rounds/start\|close\|result\|settle` | round control (admin) |
| GET | `/api/v1/admin/games` | game inventory (admin) |
| GET/PUT | `/api/v1/admin/games/{gameId}/config` | game config read (admin) / update (superadmin) |

## Teen Patti Pro legacy routes (unchanged, still served)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | liveness + config version/confirmed |
| GET | `/teen-patti-pro/` `/teen-patti-pro/game.js` | – | WebView static client |
| GET | `/api/v1/games` | – | game list |
| GET | `/api/v1/games/teen-patti-pro` | – | rules/denoms/limits/TBC/confirmed |
| POST | `/api/v1/sessions` `{launch_token}` | – | redeem token → session |
| GET | `/api/v1/sessions/{id}` | – | session info |
| GET | `/api/v1/games/teen-patti-pro/rounds/current?room=` | player | visibility-safe state |
| POST | `/api/v1/games/teen-patti-pro/rooms/{room}/bets` `{position,amount}` | player | place bet (order enforced) |
| GET | `/api/v1/games/teen-patti-pro/rounds/{id}/result?room=` | player | winners/hands (404 pre-RESULT) |
| GET | `/api/v1/games/teen-patti-pro/history?room=` | player | own bets |
| GET | `/api/v1/games/teen-patti-pro/rooms/{room}/wallet` | player | own balance |
| POST | `/api/v1/games/teen-patti-pro/rooms/{room}/reconnect` `{session_id,last_seen_seq}` | – | snapshot + missed events |
| POST | `.../rooms/{room}/rounds/start\|close` | admin | round control |
| POST | `.../rooms/{room}/rounds/result` | admin | publish result |
| POST | `.../rooms/{room}/rounds/settle` | admin | settle (idempotent) |
| GET | `/api/v1/admin/config` | admin | versioned config + TBC |
| GET | `/api/v1/admin/audit?entity=&limit=` | admin | audit trail |
| GET | `/api/v1/admin/webhooks` | admin | delivery log |

Error codes: `UNAUTHENTICATED 401, FORBIDDEN 403, NOT_FOUND 404, VALIDATION_ERROR 422,
BETTING_CLOSED 409, INSUFFICIENT_BALANCE 402, DUPLICATE_REQUEST 409, STATE_CONFLICT 409,
TBC_RULE_UNCONFIRMED 403, INTERNAL_ERROR 500`. Handle by `code`, never message text.
Round operator: `service.sweep()` closes/publishes/settles expired rounds (run 1/s).
