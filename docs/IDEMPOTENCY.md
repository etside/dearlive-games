# Idempotency rules + error codes

## Idempotency

- Every money write REQUIRES `Idempotency-Key` (bet) or a deterministic key
  (`settle:{bet_id}`, `cancel:{bet_id}`, `bet-void:{key}`).
- Keys: `bet:{client_key}` (debit + bet create), `settle:{bet_id}` (credit),
  `cancel:{bet_id}` / `bet-void:{room}:{key}` (refunds).
- Semantics: same key + same payload → replay stored result, no second money
  move. Same key + different payload → `409 DUPLICATE_REQUEST`, never execute.
- Store: Redis `SET key JSON NX EX ttl` in prod
  (`integrations/redis_store.py:RedisIdempotencyStore`); memory in sandbox.
  TTL default 24h (`claim(..., ttl_s=86400)`); settlement keys kept ≥7 days.
- Timeout rule: on timeout/transport error after a wallet write was sent,
  call `GET_TRANSACTION_STATUS {idempotency_key}` before retrying. Never
  blind-retry.
- Settlement exactly-once: `UNIQUE settlement.bet_id` (engine set +
  Postgres `UNIQUE(bet_id)`) + service `_settle_lock` + idempotent wallet
  credit. Re-settle replays identical rows.

## Error codes (handle by `code`, never message text)

| code | http | meaning | retry? |
|---|---|---|---|
| OK | 200 | success | – |
| UNAUTHENTICATED | 401 | missing/unknown session, bad launch token | no (re-launch) |
| INVALID_TOKEN | 401 | malformed/expired/replayed launch token | no (mint new) |
| FORBIDDEN | 403 | admin key missing/role lacking | no |
| TBC_RULE_UNCONFIRMED | 403 | `confirmed=False`, money blocked | no (confirm config) |
| NOT_FOUND | 404 | session/round/result unknown or not yet published | no |
| VALIDATION_ERROR | 422 | bad position/amount/denom/JSON/body | no (fix request) |
| INSUFFICIENT_BALANCE | 402 | wallet short | no (fund first) |
| BETTING_CLOSED | 409 | window shut (stake auto-refunded) | no (next round) |
| DUPLICATE_REQUEST | 409 | key reused with different payload | no (new key) |
| STATE_CONFLICT | 409 | e.g. post-debit conflict, late cancel | refund issued; new key |
| RATE_LIMITED | 429 | too many requests | yes, backoff |
| INTERNAL_ERROR | 500 | wallet/transport failure | check txn status, then retry |

Envelope on all responses:
`{success, code, message, data, serverTime, requestId}` (`common/envelope.py`).
