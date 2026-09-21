# REST API v1 — Teen Patti Pro

Envelope: `{success, code, message, data, serverTime, requestId}` (serverTime authoritative).
Auth: player `Authorization: Bearer <session_id>`; admin `X-Admin-Key` (+ RBAC matrix).
Bets: `Idempotency-Key` header REQUIRED.

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
