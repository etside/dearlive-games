# Teen Patti Pro

Production-ready 3-seat highest-hand card game with a server-authoritative
engine, a REST API, and a real-time WebSocket.

## What's included

- **Game engine** — server-authoritative; the client never decides an outcome
- **REST API** — sessions, rounds, bets, results, settlement
- **WebSocket** — hand-rolled RFC6455 push channel on port 5003
- **B2B provider API** — HMAC-signed, replay-protected, per-game scoped
- **Wallet + ledger** — idempotent debit/credit/rollback
- **Dynamic configuration** — every rule is admin-configurable
- **Test suite** — 265 passing, hermetic (no Redis/DB/network required)

## Quick start (5 minutes, zero dependencies)

Requires Python 3.12+. **No Redis, no database, no `.env`, no `npm install`.**

```bash
git clone <repo-url>
cd dearlive-games
python -m games.teen_patti_pro.api --confirmed
```

```
TeenPattiPro API: http://0.0.0.0:5002 (config tpp-1.0.0-tbc, confirmed=True, provider=no-keys)
```

That single process serves **HTTP on 5002** and the **WebSocket on 5003** —
deliberately one process, because a second socket process would hold a second
copy of the game state.

### Get a playable table

```bash
# 1. bootstrap a funded session (in-memory demo; disabled in production)
curl -s "http://127.0.0.1:5002/demo/session?room=c-room&player=stranger"
```

```json
{ "success": true, "code": "OK", "data": {
  "mode": "demo", "session_id": "dl-sess-1-...", "balance": 20000,
  "state": { "round_id": "c-room-r1", "status": "BETTING_OPEN", ... } } }
```

```bash
# 2. place a bet  (Idempotency-Key is mandatory)
SESSION=dl-sess-1-...
curl -s -X POST http://127.0.0.1:5002/api/v1/games/teen-patti-pro/rooms/c-room/bets \
  -H "Authorization: Bearer $SESSION" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-bet-1" \
  -d '{"position":"A","amount":100}'

# 3. read the table
curl -s "http://127.0.0.1:5002/api/v1/games/teen-patti-pro/rounds/current?room=c-room" \
  -H "Authorization: Bearer $SESSION"
# -> "my_bet": 100, "status": "BETTING_OPEN"

# 4. balance moved
curl -s http://127.0.0.1:5002/api/v1/wallet/balance -H "Authorization: Bearer $SESSION"
# -> "available": 19900
```

### Explore

| URL | What |
|---|---|
| <http://127.0.0.1:5002/docs> | browsable API reference |
| <http://127.0.0.1:5002/openapi.json> | OpenAPI 3.1 spec (generated) |
| <http://127.0.0.1:5002/teen-patti-pro/> | game client |
| <http://127.0.0.1:5002/greedy-monkey/> | Greedy Monkey wheel |
| <http://127.0.0.1:5002/baby-king/> | Baby King wheel |
| <http://127.0.0.1:5002/health> | liveness |

Install the only Python dependencies if you need the Postgres-backed paths:

```bash
pip install -r requirements.txt     # bcrypt, psycopg[binary]
```

## Tests

```bash
python -m pytest -q                 # 265 passed, 1 skipped
```

## Documentation

| Doc | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | components and topology |
| [API.md](docs/API.md) | REST surface, envelopes, errors |
| [WEBSOCKET.md](docs/WEBSOCKET.md) | push protocol, frames |
| [GAME_RULES.md](docs/GAME_RULES.md) | the Teen Patti Pro ruleset |
| [STATE-MACHINE.md](docs/STATE-MACHINE.md) | round lifecycle |
| [INTEGRATION.md](docs/INTEGRATION.md) | platform ↔ games contract |
| [PROVIDER-INTEGRATION.md](docs/PROVIDER-INTEGRATION.md) | B2B HMAC API guide |
| [IDEMPOTENCY.md](docs/IDEMPOTENCY.md) | keys, retries, error codes |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | local, Docker, any host, base URL, production |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | layout, tests, spec generation |
| [SECURITY.md](docs/SECURITY.md) | threat model, signing, known gaps |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | common failures |
| [UAT.md](docs/UAT.md) | acceptance matrix |
| [api/openapi.yaml](api/openapi.yaml) | generated spec |

## Two entrypoints

| | Command | Ports | Redis |
|---|---|---|---|
| **Demo** | `python -m games.teen_patti_pro.api --confirmed` | 5002, 5003 | no |
| **Staging** | `python -m staging.wsgi` | 8000 | **yes** |

Port 8000 is the *staging adapter*, a different application from the production
API on 5002. See [DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Status

Rules are **TBC pending business sign-off**. `--confirmed` is that sign-off
flag for local/demo use; real money additionally requires a production
`APP_ENV` with wallet and settlement credentials configured. See
[V1_BUSINESS_RULES_PENDING.md](V1_BUSINESS_RULES_PENDING.md).

## Licence

Proprietary — all rights reserved. See [LICENSE](LICENSE).
