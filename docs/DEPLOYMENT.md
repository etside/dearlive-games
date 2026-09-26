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

`staging/wsgi.py` is the adapter used by the Vercel deployment. It serves the
same game logic but keeps state in Redis and exposes `/api/v1/staging/*` test
routes. It **refuses to serve under `APP_ENV=production`**.

```bash
export APP_ENV=staging
export REDIS_HOST=127.0.0.1 REDIS_PORT=6379
export GAME_ADMIN_KEYS="dev-admin-key:superadmin"
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

## D. Vercel (staging)

`vercel.json` maps `/api/*` to the serverless entrypoint `api/index.py`
(10s max duration), which re-exports `staging.wsgi:app`. Static clients and
master assets are served directly, with `no-store` on `/api/*`.

| Var | Value |
|---|---|
| `APP_ENV` | `staging` |
| `REDIS_HOST` / `REDIS_PORT` | Upstash host / `6379` |
| `REDIS_USERNAME` / `REDIS_PASSWORD` | `default` / **secret** |
| `REDIS_TLS` | `true` |
| `REDIS_DB` | `0` |
| `GAME_ADMIN_KEYS` | `op-<rand>:operator,ro-<rand>:auditor,admin-<rand>:admin,super-<rand>:superadmin` |
| `COIN_CURRENCY` | `TEST` |

```
vercel build      # must pass; inspects .vercel/output
vercel deploy     # preview URL
vercel --prod     # only for an agreed promotion
```

Note that the code reads `REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD`/`REDIS_TLS`
(and optionally `REDIS_USERNAME`/`REDIS_DB`). It does **not** read
`REDIS_URL`; that name appears only in tests.

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
