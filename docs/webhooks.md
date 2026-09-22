# Webhook specification (game → DearLive)

## Delivery

- `POST {SETTLEMENT_WEBHOOK_URL}` per event, JSON body = `build_event(kind,
  data)` = `{event_id, kind, data, serverTime}`.
- Event kinds: `game.session.created, player.joined, player.left,
  round.started, bet.accepted, bet.rejected, betting.closed,
  result.published, settlement.completed, session.completed,
  round.cancelled, error`.
- `settlement.completed` data: `{round_id, settlements:[{settlement_id,
  bet_id, player_id, payout, config_version}], ...}`; `result.published`:
  `{round_id, winners}`; `bet.accepted`: `{bet_id, room_id, ...}`.

## Headers

```
Content-Type: application/json
X-Webhook-Event: <kind>
X-Webhook-Delivery: <event_id>
X-Webhook-Timestamp: <serverTime ms>
X-Webhook-Signature: hex(HMAC-SHA256(SETTLEMENT_SIGNING_SECRET, raw_body))
```

Signing method: `WEBHOOK_SIGNING_METHOD=HMAC-SHA256` (default). Secret
rotation: receiver accepts `(key_id, secret)` pairs; sender sets
`X-Webhook-Key-Id` when rotating.

## Receiver rules (DearLive implements)

1. Verify `X-Webhook-Signature` with `common/webhooks.py:verify` BEFORE
   trusting/parsing.
2. De-duplicate on `event_id` (at-least-once delivery).
3. Respond `2xx` fast; any non-2xx/transport error is retried.

## Sender rules (game implements, `common/webhooks.py:WebhookSender`)

Retry backoff `1s → 5s → 30s → 5min` (max 5 attempts), then status `dead`
(DLQ, visible at `GET /api/v1/admin/webhooks`). Every attempt is recorded in
the delivery log / `webhook_outbox` table. Outbox pattern: persist before
send so crashes never lose settlement notifications.
