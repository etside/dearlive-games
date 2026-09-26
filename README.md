# Teen Patti Pro

A single-game card platform: **3-seat highest-hand seat betting**, server
authoritative, with a REST API, a WebSocket feed, an operator admin panel and a
zero-dependency demo mode.

This is a **plug-and-play package**. It runs with nothing configured. You supply
infrastructure, secrets and a domain at deploy time; nothing you supply is
needed to try it, build it, or run its tests.

---

## What You're Getting

| | |
| --- | --- |
| **Game** | Teen Patti Pro — one game. Not traditional Teen Patti: no Blind, Chaal, Pack, Show or Sideshow. Three seats, three cards, highest approved hand takes the pot. |
| **API** | REST over HTTP, plus a WebSocket feed on the same process and the same game state. |
| **Admin** | Operator console at `/admin`, served by the same process: dashboard, profit & risk, player overrides, token packages, game rules, enable/disable, audit log, reports, settings, scheduling. |
| **Frontend** | Canvas game client with a DOM HUD (back, sound, help, menu, round pill, connection state, measured latency), lobby, and a rules page. Loading / empty / error / reconnecting states are distinct. WebView-ready — no build step, no bundler. |
| **Demo** | `--demo` runs the whole thing in memory: no Postgres, no Redis, no keys. |
| **Migrations** | Additive SQL, applied by you against your own database. |

**Requirements:** Python 3.12+. That is the whole list. The service itself needs
no third-party package; `requirements.txt` exists for the optional extras
(`bcrypt` for PIN login, `psycopg` for Postgres, `redis` for shared state).

---

## Try It in 60 Seconds (No Setup)

```bash
python -m games.teen_patti_pro.api --demo
# Open http://localhost:8000/teen-patti-pro
```

Or, if you would rather not pick a port:

```bash
./scripts/serve-demo.sh
```

That boots with every store in memory, on port **8000**, with the rules marked
confirmed so it actually deals cards. It refuses to start if
`APP_ENV=production`, and it ignores `DATABASE_URL` even if one is set in your
shell — the demo cannot accidentally become half-real.

You should see the table. Deal a hand, place a bet, watch it settle.

Verify it is alive:

```bash
curl -s http://localhost:8000/health
```

---

## Step-by-Step Integration

Ten steps. Steps 1-3 are optional if you just want to look at it; steps 4-10
are what a real deployment needs.

### Step 1 — Get the code

```bash
git clone <your-repo-url> teen-patti-pro
cd teen-patti-pro
```

Python 3.12 or newer:

```bash
python3 --version
```

### Step 2 — Run it with nothing configured

```bash
python -m games.teen_patti_pro.api --demo
```

Open <http://localhost:8000/teen-patti-pro>. If you can deal and bet, the
package is intact and everything below is configuration, not repair.

### Step 3 — Run the tests

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

The suite needs no database and no Redis. That is deliberate: you should not
have to stand up infrastructure to find out whether the code works.

### Step 4 — Create the database schema

Point at your Postgres and apply the migrations. They are additive and
idempotent, so re-running is safe.

```bash
export DATABASE_URL='postgresql://user:pass@host:5432/yourdb'
./scripts/apply-migration.sh
```

`scripts/apply-migration.sh` applies every `db/migrations/*.up.sql` in order and
verifies the result. If you prefer to do it by hand, run them in numeric order —
`001` through `007` — with `psql -v ON_ERROR_STOP=1 -f`.

> **Schema naming.** Migrations 001-006 predate the SRS and use their own table
> names (`wallet`, `coin_config`, `superadmin_audit`, `settlements`). Migration
> 007 creates the SRS names (`game_round`, `game_bet`, `game_result`,
> `game_settlement`, `config_version`, …) alongside them. Nothing was rewritten,
> so a database you migrated early keeps working. The full mapping is in
> [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#schema-alignment-legacy-names-vs-the-srs-data-model).

### Step 5 — Configure the environment

Everything below is supplied by you, at deploy time. **Do not commit any of
it.** There are no defaults for secrets: the service refuses to start in
production without them rather than inventing one.

```bash
# --- required in production ---
export DATABASE_URL='postgresql://...'          # Postgres
export REDIS_HOST='127.0.0.1'                   # or REDIS_URL
export REDIS_PORT='6379'
export GAME_JWT_SECRET='...'                    # session tokens; you generate
export OPERATOR_TOKEN_SECRET='...'              # admin session tokens
export GAME_ADMIN_KEYS='<key>:admin,<key>:operator,<key>:auditor'

# --- required when you have real money behind the game ---
export WALLET_BASE_URL='https://your-host/operator'   # your wallet callbacks
export WALLET_API_KEY='...'
export WALLET_HMAC_SECRET='...'                  # signs wallet requests
export OPERATOR_WEBHOOK_URL='https://your-host/webhooks/game'
export OPERATOR_WEBHOOK_SECRET='...'            # signs webhooks to you

# --- public addressing ---
export API_PUBLIC_URL='https://games.your-domain.com'
export WS_PUBLIC_URL='wss://games.your-domain.com'
export CORS_ORIGINS='https://your-app.your-domain.com'

# --- admin console (optional; API keys work without it) ---
export OPERATOR_PIN_HASH='<bcrypt hash>'
```

Full list with defaults and meanings: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

**The TBC gate.** While the rules are unconfirmed (`confirmed: false`) the
service refuses real-money actions with `403 TBC_RULE_UNCONFIRMED`. That is
deliberate and it is the last thing you should remove. Flip it with
`--confirmed` only once you have signed the rules off.

### Step 6 — Run it for real

```bash
python -m games.teen_patti_pro.api \
  --host 0.0.0.0 --port 5002 --ws-port 5003
```

Put a TLS-terminating reverse proxy in front. In production, `APP_ENV=production`
enables a batch of safety checks — required secrets, confirmed rules, and a
refusal to start if any are missing.

```bash
export APP_ENV=production
```

### Step 7 — Point your app at the game

The client finds the API in this order, first hit wins:

1. `?api=https://games.your-domain.com` — query parameter
2. `VITE_API_BASE` — React shell builds only
3. same origin as the page

Same-origin is the default and needs no configuration. Use `?api=` when the
client is served from a CDN and the API is not.

### Step 8 — Wire the wallet callbacks

You implement four endpoints; the game calls them. All HMAC-signed, 3 second
timeout, 3 retries with backoff.

| You implement | The game calls it to |
| --- | --- |
| `POST /operator/balance` | read a player's balance |
| `POST /operator/debit` | take a bet |
| `POST /operator/credit` | pay a win |
| `POST /operator/rollback` | undo a failed settlement |

The order of operations for a bet is fixed and audited: authenticate the
session, validate the game is enabled, validate the round is open, validate the
seat, validate the amount, validate the balance, check the idempotency key,
debit atomically, write the bet record, emit the WebSocket event.

Full contract: [docs/INTEGRATION.md](docs/INTEGRATION.md).

### Step 9 — Open the operator console

```bash
GAME_ADMIN_KEYS='<key>:admin'
```

The console is served by the same process, so there is nothing else to run:

```
https://games.your-domain.com/admin
```

Sign in with an admin key. It is held in `sessionStorage` and sent as
`X-Admin-Key`; it is never written to `localStorage`.

Nine sections: Dashboard, Profit & Risk, Player Override, Token Packages, Game
Rules, Enable/Disable, Audit Log, Reports, Settings, plus Scheduled. Roles are
`admin > operator > auditor` — there is no `superadmin`, and a key with any
other role is refused at boot.

> **Reports** renders settlement health and says so in the UI. The SRS names a
> Reports section but no reports endpoint, and this package does not invent
> one.

Full runbook: [docs/ADMIN.md](docs/ADMIN.md).

### Step 10 — Turn it on

Nothing accepts real bets until the game is enabled and the rules are confirmed:

```bash
curl -X POST -H "X-Admin-Key: $ADMIN_KEY" \
  https://games.your-domain.com/api/v1/admin/games/teen-patti-pro/enable
```

Before real traffic, work through
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#before-any-real-traffic).

---

## Troubleshooting

**`Address already in use`**
Something is on the port. `--port 8080 --ws-port 8081`, or find it:
`lsof -i :5002`.

**`Refusing production boot, missing: [...]`**
Working as intended. `APP_ENV=production` requires real values for the items
listed. It will not start with placeholders — that is the point.

**`503 database_unavailable` on every `/api/v1/admin/*`**
No `DATABASE_URL`, or the migrations were not applied. Check the response body:
it names the reason. See step 4.

**`401` on every game route**
The session token is missing or expired. Session JWTs live 15 minutes. Fetch
authoritative state and reconnect.

**`403 TBC_RULE_UNCONFIRMED`**
The rules are unconfirmed, so real money is blocked. Sign the rules off, then
start with `--confirmed`.

**`409` on a bet you did not expect**
Usually the round closed between your read and your write. Server time is
authoritative; re-read state and act on the new round.

**WebSocket connects, then goes quiet**
Check `balance.updated` and `error` are reaching the client, and that your proxy
passes WebSocket upgrades. See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

**`DATABASE_URL` set but admin routes still 503**
The URL parsed but a query failed. That is `502`, not `503` — so you are probably
hitting the no-database branch. Confirm with
`curl -H "X-Admin-Key: $KEY" .../api/v1/admin/whoami` and read
`docs/ADMIN.md` §5.

**A scheduled config change never applied**
The sweep runs every 60s. Trigger it by hand with
`POST /api/v1/admin/scheduled-changes/apply`, then read `report.failed` — a
change that failed is marked `FAILED` and is never retried automatically,
because a payload that violates a constraint will violate it identically
forever.

---

## API Reference

Full reference: [docs/API.md](docs/API.md) · machine-readable:
[api/openapi.yaml](api/openapi.yaml)

Every response uses one envelope:

```json
{
  "success": true,
  "code": "OK",
  "message": "",
  "data": {},
  "serverTime": "2026-09-26T12:00:00+00:00",
  "requestId": "uuid"
}
```

Server time is authoritative. Never trust a client clock.

### Game

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/games` | provider |
| GET | `/api/v1/games/teen-patti-pro` | provider |
| GET | `/api/v1/games/teen-patti-pro/rounds/current` | session |
| GET | `/api/v1/games/teen-patti-pro/rounds/{id}/result` | session |
| GET | `/api/v1/games/teen-patti-pro/history` | session |
| GET | `/api/v1/games/teen-patti-pro/tables/{table}/state` | session |
| POST | `/api/v1/games/teen-patti-pro/rooms/{room}/rounds/{id}/bets` | session |
| POST | `/api/v1/games/teen-patti-pro/autobet` | session |
| POST | `/api/v1/games/teen-patti-pro/autoplay` | session |

### Session

| Method | Path | Auth |
| --- | --- | --- |
| POST | `/api/v1/sessions` | provider |
| GET | `/api/v1/sessions/{id}` | session |
| POST | `/api/v1/games/{game}/sessions` | provider |
| POST | `/api/v1/games/teen-patti-pro/rooms/{room}/reconnect` | session |

### Wallet

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/wallet/balance` | session |
| GET | `/api/v1/wallet/transactions` | session |
| GET | `/api/v1/games/{game}/rooms/{room}/wallet` | session |

### Admin

Auth is `X-Admin-Key`. Reads need `auditor`, writes need `admin`. Roles are
`admin`, `operator`, `auditor` — there is no `superadmin`. An entry with any
other role is refused at boot with a warning, not silently accepted.

| Method | Path |
| --- | --- |
| GET | `/api/v1/admin/whoami` |
| GET | `/api/v1/admin/dashboard` |
| GET | `/api/v1/admin/settlement-health` *(works with no database)* |
| GET · PUT | `/api/v1/admin/profit-risk` |
| POST | `/api/v1/admin/profit-risk/simulate` |
| GET · POST · DELETE | `/api/v1/admin/player-overrides` |
| GET · POST | `/api/v1/admin/packages` |
| PUT · DELETE | `/api/v1/admin/packages/{id}` |
| GET · PUT | `/api/v1/admin/settings` |
| GET · POST · DELETE | `/api/v1/admin/scheduled-changes` |
| POST | `/api/v1/admin/scheduled-changes/apply` |
| POST | `/api/v1/admin/games/{slug}/enable` |
| POST | `/api/v1/admin/games/{slug}/disable` |
| GET · PUT | `/api/v1/admin/games/{id}/config` |
| GET | `/api/v1/admin/audit` |
| GET | `/api/v1/admin/players` |
| GET | `/api/v1/admin/players/{id}/override` |
| POST | `/api/v1/admin/players/{id}/override` |
| DELETE | `/api/v1/admin/players/{id}/override` |
| GET | `/api/v1/admin/games/{slug}/rules` |
| PUT | `/api/v1/admin/games/{slug}/rules` |
| GET | `/api/v1/admin/players/{id}/appearance` |
| PUT | `/api/v1/admin/players/{id}/appearance` |

### WebSocket

`ws://host:5003` in development, `wss://` in production. Authenticate in the
connect payload with the session JWT. Events: `round.created`, `round.opened`,
`round.updated`, `betting.closed`, `result.processing`, `result.declared`,
`settlement.started`, `settlement.completed`, `balance.updated`, `round.closed`,
`error`. Every event carries `roundId`, `serverTime` and `requestId`.

On reconnect, fetch authoritative state and replay from the snapshot — do not
assume the events you missed.

### Idempotency

`Idempotency-Key` is **required on financial writes**. A duplicate returns the
cached response instead of moving money twice. Backed by Redis with a 24 hour
TTL. In the database, `game_settlement.bet_id` is `UNIQUE` and
`game_bet.idempotency_key` is unique where present, so a double payout is
impossible even across a restart.

---

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Step-by-step deploy, every environment variable, schema alignment |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | Platform ↔ games contract, wallet callbacks, webhooks |
| [docs/ADMIN.md](docs/ADMIN.md) | Admin keys, roles, every endpoint, scheduling |
| [docs/API.md](docs/API.md) | Endpoint reference |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, data model, key decisions |
| [docs/GAME_RULES.md](docs/GAME_RULES.md) | The ruleset, traceable to code |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common failures |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, what is enforced where |

## Repository Layout

```
games/teen_patti_pro/     engine, service, API, WebSocket, client
common/                   config, wallet, idempotency, auth, admin store
provider/                 DearLive platform integration
integrations/             store factories (in-memory, Redis, Postgres)
db/migrations/            additive SQL, 001-007
scripts/                  setup-dev.sh, apply-migration.sh, serve-demo.sh
docs/                     the documents listed above
api/openapi.yaml          machine-readable spec
```

## Scripts

| Script | What it does |
| --- | --- |
| `scripts/serve-demo.sh` | Zero-dependency demo on port 8000 |
| `scripts/apply-migration.sh` | Applies and verifies migrations against your `DATABASE_URL` |
| `scripts/setup-dev.sh` | `gh auth` + dependencies + `.env` bootstrap |

## Deployment

Push-to-deploy is wired but **inert until you set `DEPLOY_HOST`**. See
[.github/workflows/deploy.yml](.github/workflows/deploy.yml) for the secret names
(`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_PATH`,
`DEPLOY_KNOWN_HOSTS`, `DEPLOY_RESTART_CMD`). Until then every job skips.

Deploying any other way is fine — the package is host-agnostic and has no
provider-specific code.

## License

Proprietary. All rights reserved. See [LICENSE](LICENSE).
