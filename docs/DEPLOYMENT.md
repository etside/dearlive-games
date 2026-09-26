# Deployment

Three supported shapes. Pick one; they differ only in which process you start
and what backing stores you point it at.

| Shape | Command | Ports | Needs Redis | Audience |
|---|---|---|---|---|
| **A. Zero-dep demo** | `python -m games.teen_patti_pro.api --confirmed` | 5002 HTTP, 5003 WS | no | first look, CI, demos |
| **B. Staging adapter** | `python -m staging.wsgi` | 8000 HTTP | **yes** | UAT against production-shaped routes |
| **C. Container** | `docker compose -f docker-compose.prod.yml up -d` | 80/443 → 5002/5003 | via compose | VPS |

> **Port note.** The production API listens on **5002**, not 8000. Port 8000
> belongs to the *staging adapter* (`staging/wsgi.py`). The two are different
> applications; see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## A. Zero-dependency demo

No Redis, no database, no provider keys, no `.env`. Everything runs in one
process against in-memory stores.

```bash
python -m games.teen_patti_pro.api --confirmed
# TeenPattiPro API: http://0.0.0.0:5002 (config tpp-1.0.0-tbc, confirmed=True, provider=no-keys)
```

Then bootstrap a funded, playable session:

```bash
curl "http://127.0.0.1:5002/demo/session?room=c-room&player=stranger"
# -> {"mode":"demo","session_id":"dl-sess-...","balance":20000,"state":{...}}
```

`/demo/session` is **hard-disabled when `APP_ENV=production`** and returns 404
there. It mints a real launch token and opens a real session, so every
downstream route authenticates normally.

Useful flags: `--host`, `--port`, `--ws-port`, `--no-ws`, `--no-sweeper`,
`--confirmed`.

### What `--confirmed` means
`--confirmed` is the TBC business sign-off for the ruleset. It is required to
place bets, and a production boot refuses to start without it. **It does not
enable real money** — that additionally requires a real wallet base URL and
settlement secrets. Never pass it with real provider credentials attached.

---

## B. Staging adapter (requires Redis)

`staging/wsgi.py` is the staging/UAT adapter. It serves the
same game logic but keeps state in Redis and exposes `/api/v1/staging/*` test
routes. It **refuses to serve under `APP_ENV=production`**.

```bash
export APP_ENV=staging
export REDIS_HOST=127.0.0.1 REDIS_PORT=6379
export GAME_ADMIN_KEYS="dev-admin-key:admin"
python -m staging.wsgi
# staging server   http://0.0.0.0:8000
#   redis          127.0.0.1:6379   (required)
```

It uses a threading WSGI server so a WebSocket and REST polling can be in
flight at once.

Redis is **mandatory** — without it every `/api/v1/*` route returns
`503 staging backend unavailable: redis ...`. This is deliberate: fail loudly
rather than silently serve a split-brain table.

```bash
# get a funded session
TOKEN=$(curl -s -XPOST http://127.0.0.1:8000/api/v1/staging/test-login \
  -H 'Content-Type: application/json' \
  -d '{"player":"stranger","room":"c-room","game":"teen-patti-pro"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["launch_token"])')
```

---

## C. Container / VPS

The repository root already ships a production `Dockerfile`. **Do not add a
second process.** It runs the API *and* the WebSocket gateway in a single
process on purpose: a second socket process would hold a second in-memory game
state, and the two would diverge.

```
EXPOSE 5002 5003
HEALTHCHECK  GET /api/v1/provider/health
CMD python -m games.teen_patti_pro.api --host 0.0.0.0 --port 5002 --ws-port 5003 --confirmed
```

Minimal compose (nginx terminates TLS and proxies to the app):

```yaml
services:
  app:
    build: .
    restart: always
    env_file: .env
    expose: ["5002", "5003"]
    depends_on: [redis]
  nginx:
    image: nginx:alpine
    restart: always
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/letsencrypt:ro
    depends_on: [app]
  redis:
    image: redis:7-alpine
    restart: always
    command: redis-server --appendonly yes --maxmemory 512mb
    volumes: [redis-data:/data]
volumes: { redis-data: }
```

nginx routes `api.<domain>` → `app:5002` and `ws.<domain>` → `app:5003` with
`proxy_http_version 1.1`, `Upgrade`/`Connection` headers, and a long
`proxy_read_timeout` for the socket.

### Production boot gate

`APP_ENV=production` **refuses to start** unless all nine are present and
`--confirmed` is passed:

```
admin_keys  database_url  dearlive_api_base_url  games_base_url
provider_api_keys  provider_public_base_url  settlement_signing_secret
settlement_webhook_url  wallet_base_url
```

This is the real-money gate. A refusal lists exactly what is missing.

---

## D. Any host

The server is host-agnostic: it is a WSGI app plus one process with an
attached WebSocket. Pick whichever fits the deployment.

| Host | Command |
|---|---|
| Local / a VM / any VPS | `python3 -m staging.wsgi` behind nginx, or the container in section C |
| Embedded in the DearLive app | mount the repo and run `python3 -m games.teen_patti_pro.api`; the app talks to it over HTTP |
| Serverless / FaaS | `api/index.py` exposes a WSGI callable; any WSGI adapter will serve it |
| Zero-infrastructure demo | `python3 -m games.teen_patti_pro.api --confirmed` (no Redis, no keys) |

Production runs the **provider API** (`games.teen_patti_pro.api`, HTTP 5002 +
WebSocket 5003). The staging adapter (`staging.wsgi`, port 8000) is for
staging/UAT and refuses to boot under `APP_ENV=production`.

### Setting the base URL

Nothing in this repository hardcodes a hostname. The clients resolve the API
in this order:

1. `?api=<origin>` on the game client URL, e.g.
   `https://your-host/teen-patti-pro/?api=https://api.your-host`
2. `VITE_API_BASE` at build time, for the `apps/*` frontends
3. The page origin, which is the default

So serving the API and the client from one host needs no configuration at
all. Only a cross-origin setup needs step 1 or 2.

The server also needs to know its own public address so it can build launch
links: set `PROVIDER_PUBLIC_BASE_URL` to the externally reachable API origin.

### TLS

The app speaks plain HTTP and expects a reverse proxy to terminate TLS. Never
expose 5002 or 5003 directly to the internet. The bundled `Dockerfile` plus an
nginx container is the shortest path; see section C.

---

## Health & observability

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness for the API process |
| `GET /api/v1/provider/health` | dependency-aware health (Docker `HEALTHCHECK` uses this) |
| `GET /docs` · `GET /openapi.json` | live API docs + generated spec |

`/api/v1/provider/health` reports `degraded` when a dependency is missing and
lists the reason (for example an absent Postgres driver). That is expected
without a `DATABASE_URL`.

## Before any real traffic

- Swap every in-memory store for the Redis/Postgres implementation. The
  interfaces are identical; only the constructor in `integrations.build_stores()`
  changes.
- Terminate TLS in front of 5002/5003.
- Set `REDIS_TLS=true` and use credentials — never a bare Redis.
- Keep settlement secrets out of the WebView bundle.

---

## External dependencies (supplied by the integrator)

The DearLive app developer provides infrastructure and secrets at deploy
time. The table lists the variable names this codebase **actually reads** —
several common names (`JWT_SECRET`, `SESSION_TOKEN_SECRET`, `CORS_ORIGINS`,
`REDIS_URL`, `ENABLE_REAL_MONEY`) do not exist here, and setting them would
have no effect. See "Names that do not apply" below.

| Env var | Purpose | Required |
|---|---|---|
| `DATABASE_URL` | Postgres (Neon/Supabase/RDS) for admin reads | **yes** for admin routes |
| `REDIS_HOST` | Redis host | yes for the staging adapter |
| `REDIS_PORT` | Redis port (default `6379`) | yes for the staging adapter |
| `REDIS_TLS` | `true` for Upstash-style TLS | no (`false` locally) |
| `REDIS_USERNAME` / `REDIS_PASSWORD` | Redis credentials | when the server requires auth |
| `REDIS_DB` | Redis logical database | no (`0`) |
| `OPERATOR_PIN_HASH` | bcrypt hash of the operator PIN | yes to log in |
| `OPERATOR_TOKEN_SECRET` | HS256 key for operator bearer tokens | yes |
| `ADMIN_PIN_HASH` | bcrypt hash of the admin PIN (optional) | no |
| `ADMIN_TOKEN_SECRET` | HS256 key for admin PIN sessions (optional) | no |
| `GAME_ADMIN_KEYS` | `key:role` pairs → auditor/operator/admin | **yes** |
| `PROVIDER_API_KEYS` | B2B HMAC keys (`key_id:secret`) | yes for the provider API |
| `PROVIDER_PUBLIC_BASE_URL` | Public base URL used in launch links | recommended |
| `SETTLEMENT_SIGNING_SECRET` | Signs settlement webhooks | production only |
| `WALLET_BASE_URL` / `WALLET_API_KEY` | Real wallet instead of the mock | production only |
| `APP_ENV` | `production` activates the boot gate | yes in production |

### Names that do not apply

These appear in some integration runbooks but are **not read by this codebase**.
Do not set them expecting an effect:

`JWT_SECRET`, `JWT_REFRESH_SECRET`, `SESSION_TOKEN_SECRET`, `CORS_ORIGINS`,
`API_PUBLIC_URL`, `WS_PUBLIC_URL`, `ENABLE_REAL_MONEY`, `ENABLE_DEMO_MODE`,
`REDIS_URL`.

Notes on the ones with a real equivalent:

- **Token signing** uses `OPERATOR_TOKEN_SECRET` / `SUPERADMIN_TOKEN_SECRET`
  via `common/jwtx.py` (HS256, standard library only). There is no refresh
  token; sessions are stateless with a 24h expiry.
- **Redis** is configured with discrete `REDIS_HOST` / `REDIS_PORT` /
  `REDIS_TLS` / `REDIS_USERNAME` / `REDIS_PASSWORD` / `REDIS_DB`. A single
  `REDIS_URL` is never read.
- **Public URLs** are `PROVIDER_PUBLIC_BASE_URL` (server-side) and
  `PROVIDER_CLIENT_PATH` (launch path). The client reads them from the launch
  response rather than from its own build-time config.
- **Real money** is gated by `APP_ENV=production` plus the nine-variable boot
  gate, not by a feature flag. `ENABLE_REAL_MONEY` is not read.
- **Demo mode** is the absence of `APP_ENV=production`. There is no
  `ENABLE_DEMO_MODE` flag; `GET /demo/session` 404s under production.

## Zero-dependency demo (no infrastructure at all)

```bash
python3 -m games.teen_patti_pro.api --confirmed
curl "http://127.0.0.1:5002/demo/session?room=c-room&player=stranger"
```

No database, no Redis, no provider keys, no `.env`. There is **no `--demo`
flag** — the demo route is simply active whenever `APP_ENV` is not
`production`, and it returns 404 when it is.

## DearLive integration — infrastructure setup

1. The integrator provides `DATABASE_URL`, Redis host/port, and the secrets
   above.
2. Apply the schema:

   ```bash
   export DATABASE_URL='postgresql://...'
   bash scripts/apply-migration.sh
   ```

   The script is idempotent and verifies that `profit_risk_config`,
   `player_override`, `vip_tier` and `withdrawal_request` exist afterwards.
3. Fill in `.env` (`bash scripts/setup-dev.sh` creates it from the template).
4. Start the stack: `python3 -m staging.wsgi`
5. Verify: `curl localhost:8000/api/v1/health` → `status: "ok"` with
   `services.database.status == "ok"`.
6. Point the frontend at the deployed API.

### Health contract

```json
{
  "status": "ok | degraded | unavailable",
  "version": "<git sha>",
  "timestamp": "<ISO-8601 UTC>",
  "uptime_seconds": 0,
  "services": {
    "database":  { "status": "ok|error|unavailable", "reason": "..." },
    "redis":     { "status": "ok|error|unavailable", "reason": "..." },
    "websocket": { "status": "ok|error|not_configured", "reason": "..." }
  }
}
```

- `ok` — database and Redis both reachable.
- `degraded` — a dependency is absent but nothing errored (typically
  `DATABASE_URL not configured`). HTTP **503**.
- `unavailable` — a probe ran and failed. HTTP **503**.
- HTTP is 200 only for `ok`, so a load balancer can use the status code alone.

## GitHub setup for the integrator

Uses the `gh` CLI; no personal access token is ever handled by hand.

```bash
gh repo clone <org>/<name> platform
cd platform
gh auth setup-git
git remote -v
```

Automated bootstrap:

```bash
bash scripts/setup-dev.sh
```

This checks the toolchain, runs `gh auth login --web` if needed, points git at
the gh credential helper, installs dependencies, and seeds `.env` (mode 600)
from `.env.example`. It never writes a secret.

### Repository secrets

Set them with `gh secret set` so they never touch a shell history or a file:

```bash
gh secret set DATABASE_URL           --body "$DATABASE_URL"
gh secret set REDIS_PASSWORD         --body "$REDIS_PASSWORD"
gh secret set OPERATOR_TOKEN_SECRET  --body "$(openssl rand -hex 32)"
gh secret set SUPERADMIN_TOKEN_SECRET --body "$(openssl rand -hex 32)"
gh secret set SETTLEMENT_SIGNING_SECRET --body "$(openssl rand -hex 32)"
```

PIN hashes are bcrypt, generated locally so the plaintext PIN never leaves the
machine:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR-PIN', bcrypt.gensalt()).decode())"
gh secret set OPERATOR_PIN_HASH  --body "<that hash>"
```

### Trigger and watch a deploy

```bash
gh workflow run deploy.yml
gh run watch
gh run list --limit 3
```
