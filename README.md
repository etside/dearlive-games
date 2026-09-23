# DearLive Games — Teen Patti Pro (B2B Game Provider API)

Server-authoritative Teen Patti Pro exposed as a provider API: one API
connection and one API key per operator, signed requests, idempotent wallet
operations, and an operator-owned balance (DearLive). Full guide:
**[`docs/provider-integration.md`](docs/provider-integration.md)**.

## Quick start (local, Termux-friendly)
```bash
cp .env.example .env.local          # set PROVIDER_API_KEYS + PROVIDER_API_SECRET
export PROVIDER_API_KEYS=tp_demo:s3cr3t
export PROVIDER_PUBLIC_BASE_URL=http://127.0.0.1:5002
python3 -m games.teen_patti_pro.api --port 5002 --ws-port 5003 --confirmed
# API + WebSocket run in ONE process so they share game state.
# Docs: http://127.0.0.1:5002/docs  ·  Spec: /openapi.json  ·  Health: /api/v1/provider/health
```
`--confirmed` is the TBC business sign-off; a production boot refuses to start
without it. Omit `--no-ws` to also serve the WebSocket gateway in-process.

Docker:
```bash
docker build -t teen-patti-provider .
docker run --rm -p 5002:5002 -p 5003:5003 --env-file .env.local teen-patti-provider
```

Signed request from curl (no dependencies):
```bash
python3 tools/provider_sign.py GET /api/v1/games
python3 tools/provider_sign.py POST /api/v1/sessions \
  --body '{"player_id":"player_10025","game_code":"teen_patti_pro","currency":"COIN"}'
```
Python reference client: `sdk/provider_client.py`. Postman (self-signing
pre-request script): `docs/postman_collection.json`.

V1 scope: the existing engine is wrapped unchanged, so the betting phase is
`action: "bet"` only. There are no player turns and no fold/show actions, and
those events are documented as absent rather than simulated.

## Tests
```bash
python3 -m unittest discover -s tests      # full suite
python3 -m unittest tests.test_provider_api
```

## Layout
`provider/` B2B surface (auth, sessions, tables, wallet ledger, OpenAPI) ·
`common/` engine iface, lifecycle, envelope, idempotency, wallet/session iface,
audit, HMAC webhooks · `games/teen_patti_pro/` config, engine, service, api, ws,
client · `games/wheel_common/` wheel engines · `staging/` serverless adapter ·
`admin/` RBAC · `tools/` JEV reviews, artifact generator, signing CLI ·
`sdk/` clients · `tests/` · `docs/`.

## JEV role
Advisory review layer (`http://127.0.0.1:8080` Zen): requirements, design, code,
tests. Stored in `tools/jev_reviews/*.result.json`. Real finds fixed so far:
close-vs-bet race (mutex+invariant), tie unfairness (dead-heat split), unlocked
idempotency claim, uncompensated post-debit failure, unlocked settle credits,
missing timer sweep (implemented). JEV never touches RNG/state/money.

## Integrate into DearLive
Provider integration: `docs/provider-integration.md`. Earlier host-app
integration notes: `docs/integration-contract.md`. Specs: `docs/openapi.yaml`
(OpenAPI 3.1, generated from `provider/spec.py`), `docs/postman_collection.json`,
`docs/webhooks.md`, `docs/realtime.md`, `docs/errors-idempotency.md`.
Env: copy `.env.example` → `.env.local` (sandbox) / secrets manager
(staging/prod); `APP_ENV=production` refuses to boot when values are missing.
Adapters: `integrations/dearlive.py` (HTTP wallet DearLive owns balances) +
`integrations/redis_store.py` (shared-Redis tokens/idempotency) selected by
`integrations/build_stores()`; mocks stay for sandbox only. Game-side records
schema: `db/schema.sql` (no balances). Legacy host-app SDKs:
`sdk/javascript-client.js`, `sdk/python_client.py`.
