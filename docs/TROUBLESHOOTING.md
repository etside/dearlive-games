# Troubleshooting

## Known limitation: no automated visual render

The admin panel and the game HUD are verified **statically** — DOM structure,
every endpoint each one calls, the `getElementById` wiring, and the path
traversal guard. They are **not** verified by an automated browser render: the
environment this package was built in cannot run a headless browser (Chrome
aborts at startup), so no screenshot or DOM-dump test exists.

What that means in practice:

- Structure, wiring and security are covered by the test suite and will catch
  regressions.
- **Visual appearance is unverified.** Fonts may not render as intended, and
  layout has never been seen on a real device or browser.

Visual validation is expected during integration. If something looks wrong,
that is a real finding, not a known false positive.

## `python -m staging.wsgi` does nothing

**Expected before the `__main__` runner was added.** It should now print a
banner. If it exits silently you are on an old checkout:

```bash
python -m staging.wsgi     # -> "staging server   http://0.0.0.0:8000"
```

## Every `/api/v1/*` route returns 503

Almost always **Redis**.

```
staging backend unavailable: redis 127.0.0.1:6379: [Errno 111] Connection refused
```

The staging adapter requires Redis and fails loudly rather than serving a
split-brain table.

```bash
redis-cli ping                                  # expect PONG
redis-server --daemonize yes --save '' --appendonly no
```

If you only want to look around, use the zero-dep path instead — it needs no
Redis at all:

```bash
python -m games.teen_patti_pro.api --confirmed
```

## `Refusing production boot, missing: [...]`

Intentional. `APP_ENV=production` requires nine variables and `--confirmed`.
The message lists exactly what is absent — see
[DEPLOYMENT.md](DEPLOYMENT.md#production-boot-gate).

`staging/wsgi.py` is the *staging* adapter and refuses to serve at all under
production; run `games.teen_patti_pro.api` instead.

## `401 signed operator request or launch_token is required`

You are hitting a provider route with no credential. Three ways in:

- **Demo:** `GET /demo/session?room=c-room&player=dev` (non-production only) —
  returns a funded `session_id`.
- **Staging:** `POST /api/v1/staging/test-login` then
  `POST /api/v1/sessions` with the returned `launch_token`.
- **B2B:** sign the request — see [PROVIDER-INTEGRATION.md](PROVIDER-INTEGRATION.md).

## `402 INSUFFICIENT_BALANCE` on a correct bet

The engine debits the real wallet. In the demo the balance comes from
`/demo/session`; in staging from the faucet at
`/api/v1/staging/test-wallet/grant`. A hand-built session has no funds.

## `409 BETTING_CLOSED` on every bet

Two causes:

1. No round is open. Round creation belongs to the **operator** —
   `POST /api/v1/games/teen-patti-pro/rooms/<room>/rounds/start`. Reads do not
   auto-start a round by design (see [STATE-MACHINE.md](STATE-MACHINE.md)).
2. A room-parse bug, which previously matched `greedy` before backtracking and
   addressed a phantom room. Fixed; if you see this on an old build, update.

## `rounds/current` returns `round: null`

Expected until a round is started. The read path deliberately does **not**
bootstrap a round — auto-starting on read broke the operator ticker, because
`start_round()` rejects a room that already has a live round. Start one as the
operator.

## Unknown route drops the connection instead of returning 404

Fixed. `do_GET` was missing its 404 fallthrough (it returned without writing a
response, so the socket closed — seen as `RemoteDisconnected`). If you still see
it, you are pre-fix; `do_POST`, `do_PUT`, and `do_DELETE` always had one.

## WebSocket connects then closes

- Confirm you are on **5003**, not 5002. `/health` and `/openapi.json` are on
  5002; the socket is a separate port in the same process.
- The handshake must send `Upgrade: websocket`, `Connection: Upgrade`,
  `Sec-WebSocket-Key`, and `Sec-WebSocket-Version: 13`. Expect
  `HTTP/1.1 101 Switching Protocols`.
- A reverse proxy must forward `Upgrade`/`Connection` and allow a long
  `proxy_read_timeout`. A 60s default will drop an idle table.

## Two players see different game state

You started the API twice. HTTP and WebSocket **must** share one process — a
second process holds its own in-memory state. Run exactly one
`games.teen_patti_pro.api`; do not split the socket into its own service.

## `node_modules` keeps showing up in `git status`

It should be ignored. If files are already tracked, `.gitignore` does not apply
to them:

```bash
git rm -r --cached node_modules packages/*/node_modules apps/*/node_modules
git commit -m "chore: untrack node_modules"
```

## Health reports `degraded`

`/api/v1/provider/health` lists the failing dependency inline. A missing
Postgres driver or absent `DATABASE_URL` is the usual cause and is expected in
demo mode. `/health` is the plain liveness probe.

## Spec looks stale

`api/openapi.yaml` is generated. Edit `provider/spec.py`, then:

```bash
python tools/gen_provider_artifacts.py
```

## Tests

```bash
python -m pytest -q          # 265 passed, 1 skipped
```

The suite is hermetic — no Redis, database, or network needed. If a test fails
only on your machine, check for a stray process on 5002/5003/8000.
