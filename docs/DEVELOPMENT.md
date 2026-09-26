# Development

## Requirements

- Python **3.12+** (`.python-version` pins 3.14)
- Node 18+ — only for the optional `apps/*` frontends and asset tooling
- Redis — **only** for the staging adapter (shape B). The zero-dep demo does
  not need it.

The server is deliberately **standard-library only**. The Python dependency
list is tiny on purpose:

```bash
pip install -r requirements.txt      # bcrypt, psycopg[binary]
```

## Run it

Two entrypoints, described in [DEPLOYMENT.md](DEPLOYMENT.md).

```bash
# A. zero-dependency demo — no Redis, no .env
python -m games.teen_patti_pro.api --confirmed
curl "http://127.0.0.1:5002/demo/session?room=c-room&player=dev"

# B. staging adapter — needs Redis
export APP_ENV=staging REDIS_HOST=127.0.0.1 REDIS_PORT=6379
export GAME_ADMIN_KEYS="dev-admin-key:admin"
python -m staging.wsgi               # http://0.0.0.0:8000
```

Both print a banner with the resolved host, port, and (for B) the Redis target.

## Tests

```bash
python -m pytest -q          # 265 passed, 1 skipped
python -m pytest tests/test_provider_api.py -q
python -m pytest -k idempotency -q
```

The suite is hermetic — it does not need Redis, a database, or network.

### Layout

| Path | Contents |
|---|---|
| `games/teen_patti_pro/api.py` | HTTP + WS handler, all routing (the main file) |
| `games/teen_patti_pro/service.py` | game engine: rounds, bets, settlement |
| `games/teen_patti_pro/ws.py` | hand-rolled RFC6455 WebSocket, port 5003 |
| `games/wheel_common/` | Greedy Monkey / Baby King engines |
| `common/` | config, envelopes, errors, session/token stores |
| `provider/` | B2B contract: router, HMAC auth, ledger, spec |
| `integrations/` | store factories, Redis impls, DearLive wallet |
| `staging/wsgi.py` | staging/UAT WSGI adapter (`python3 -m staging.wsgi`) |
| `api/index.py` | generic WSGI entry for serverless hosts |
| `tools/` | spec generator, browser QA, asset pipeline |

## Regenerating the API spec

`api/openapi.yaml` and `docs/postman_collection.json` are **generated** — never
hand-edit them. Edit `provider/spec.py`, then:

```bash
python tools/gen_provider_artifacts.py
# wrote api/openapi.yaml
# wrote docs/postman_collection.json
```

The running server serves the same spec live at `/openapi.json`, with browsable
docs at `/docs`.

## Frontend workspaces

```bash
npm install
npm run dev:player        # or dev:admin / dev:super-admin
npm run build:all
npm run lint
npm test
```

## Conventions

- **Server-authoritative.** The client never decides an outcome. The engine
  deals, scores, and settles; the client only renders state.
- **Envelopes everywhere.** Every response is
  `{"success", "code", "message", "data", "serverTime", "requestId"}`.
- **Idempotency is mandatory** on any money or bet mutation. Reject a missing
  `Idempotency-Key` rather than guessing.
- **Secrets never reach the browser.** No keys in the WebView bundle, no
  secrets in `apps/*/dist`.
- **No `node_modules` in git.** If a JS dependency shows up as modified in
  `git status`, something bypassed `.gitignore`.

## Adding a route

1. Add the path to `do_GET`/`do_POST` in `games/teen_patti_pro/api.py`.
2. Envelope any response via `self.ok(...)`; raise `ServiceError` for failures
   so `self.fail()` renders the right code.
3. If the route is part of the B2B contract, add it to `provider/spec.py`
   **and** the `ROUTES` table in `provider/router.py`, then regenerate the spec.
4. Add a test in `tests/`.
