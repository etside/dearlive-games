# WebSocket — Teen Patti Pro push (`ws.py`, stdlib RFC6455)

`C→S {"action":"subscribe","room","session"}` → `S→C {"kind":"snapshot","data"}`.
Push: `{"kind":"event","data":{kind, seq, serverTime, ...}}` for
`round.started|round.tick|bet.accepted|betting.closed|result.published|
settlement.completed|session.completed|round.cancelled`.
`round.tick` 1/s/room while subscribed (serverTime + betting_end_at; client
derives countdown from server values only). `{"action":"ping"}` ↔ `{"kind":"pong"}`.
Reconnect: re-subscribe, then REST `/reconnect` with last `seq` for missed events
(per-round log, cap 500). Bets are REST-only (idempotency headers).
