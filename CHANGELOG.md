# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-09-26

Plug-and-play release for a single game: **Teen Patti Pro**. Everything a
client needs is in the repository, and nothing in it requires infrastructure
they have not yet configured.

### Game
- Teen Patti Pro only. 3 seats, 3 cards, highest approved hand wins the pot.
- 30s betting window by default, configurable.
- Hand rankings: Trail > Pure Sequence > Sequence > Color > Pair > High Card,
  with an admin-configurable order and tie policy.
- Server-authoritative throughout. The client never computes a result.
- CSPRNG shuffle with `seed_hex` and `deck_commitment` stored per round, so
  any round is reproducible for review.
- Zero-dependency demo mode: `--demo` runs in memory on port 8000 and refuses
  to start under `APP_ENV=production`.

### API
- REST + WebSocket served by one process, sharing one game state.
- Envelope `{success, code, message, data, serverTime, requestId}` with
  ISO8601 `serverTime` and `serverTimeMs` for arithmetic.
- `Idempotency-Key` honoured on financial writes, backed by Redis in
  production and enforced again in the database.
- Immutable wallet ledger with all eight transaction types and a balance that
  reconciles against the transaction sum.
- Settlement is idempotent on `bet_id`, enforced by a database constraint.

### Admin
- Operator console at `/admin`, served by the same process. Nine sections plus
  scheduling: dashboard, profit & risk, player overrides, token packages, game
  rules, enable/disable, audit log with CSV export, reports, settings.
- API-key auth with an `admin > operator > auditor` ladder. Unknown roles are
  refused at boot rather than at first request.
- Every admin read answers `503` with a reason when no database is configured,
  never an empty payload that would read as "nothing to report".
- Config changes are versioned; a round keeps the version it started with.

### Client
- Canvas game client with a DOM HUD: back, sound, help, menu, round pill,
  connection state and measured latency.
- Distinct loading, empty, error and reconnecting states.
- Red / blue / green seats, reduced-motion support, 360px → 1440px.

### Integration
- `README.md` — a ten-step client guide, troubleshooting and API reference.
- `scripts/serve-demo.sh`, `scripts/apply-migration.sh`, `scripts/setup-dev.sh`.
- Migrations 001-007, additive and idempotent. 007 creates the SRS-named
  tables alongside the earlier ones.
- Host-agnostic. `.github/workflows/deploy.yml` is inert until the client sets
  `DEPLOY_HOST`, then rsyncs over SSH to a host they nominate.

### Removed
- Greedy Monkey, Baby King and the shared wheel engine, with their assets,
  routes, provider bindings and tests.
- The `superadmin` role. The role ladder is `admin > operator > auditor`.
- All Vercel configuration, workflows and hardcoded hosts.

## [0.1.0] — 2026-09-26

Superseded by v1.0.0. Kept for history; note the wheel games it describes were
removed before v1.0.0.

First internal release. Server-authoritative Teen Patti Pro with a REST API,
a real-time WebSocket, and a B2B provider integration.

### Added

**Game**
- Teen Patti Pro engine: 3 seats (A/B/C), highest-hand wins, seat betting.
- Round lifecycle `BETTING_OPEN → CLOSED → RESULT → SETTLED`, plus a background
  sweeper (`_start_sweeper`) for timer expiry on long-lived servers.
- Configurable denomination set (20/100/500/1000) and table config via admin
  routes.
- ~~Greedy Monkey and Baby King wheel games~~ — removed before v1.0.0; this
  package ships exactly one game.

**API**
- REST surface for sessions, rounds, bets, results, history, and wallet
  balance, with a uniform envelope
  (`success`/`code`/`message`/`data`/`serverTime`/`requestId`).
- B2B provider API with HMAC-SHA256 request signing: `X-API-Key`,
  `X-Timestamp`, `X-Nonce`, `X-Signature` over
  `METHOD \n PATH \n TIMESTAMP \n NONCE \n SHA256(body)`.
- Replay protection via a nonce store, a request freshness window, and
  per-key rate limiting.
- Idempotent wallet `debit` / `credit` / `rollback`, enforced by
  `Idempotency-Key`.
- Generated OpenAPI 3.1 spec at `api/openapi.yaml` plus a Postman collection,
  both produced by `tools/gen_provider_artifacts.py` from `provider/spec.py`.
- Browsable docs at `/docs`; spec at `/openapi.json`.

**Real-time**
- RFC6455 WebSocket gateway on port 5003, served in the same process as the
  HTTP API so both share one game state.

**Operations**
- Production `Dockerfile`: single process, non-root (uid 10001), healthcheck on
  `/api/v1/provider/health`.
- Vercel serverless deployment via `api/index.py`.
- Production boot gate refusing to start without nine required variables and
  `--confirmed`.

**Demo and docs**
- `GET /demo/session` — zero-dependency bootstrap returning a funded session
  and an open round. Hard-disabled when `APP_ENV=production`.
- `python -m staging.wsgi` runner with a threading WSGI server.
- Documentation set: architecture, API, WebSocket, game rules, state machine,
  integration, provider integration, idempotency, deployment, development,
  security, troubleshooting, UAT.
- Proprietary licence.

### Fixed

- `do_GET` had no 404 fallthrough. Unmatched paths returned without writing a
  response, so the socket closed and clients saw `RemoteDisconnected` instead
  of `404 NOT_FOUND`. `do_POST`, `do_PUT`, and `do_DELETE` were unaffected.
- Room parsing matched `greedy` before backtracking to the short form,
  addressing a phantom room with no open round; every bet returned
  `409 BETTING_CLOSED`.

### Changed

- Round creation is owned by the operator (`rounds/start`). A read
  (`rounds/current`) deliberately does **not** auto-start a round: doing so
  broke the operator ticker, because `start_round()` rejects a room that already
  has a live round.
- Documentation promoted to a single canonical set (uppercase filenames);
  `docs/openapi.yaml` moved to `api/openapi.yaml`.
- `.gitignore` hardened to cover nested `node_modules`, TLS material, logs, and
  Python caches while keeping `.env.example` tracked.

### Known issues

See [docs/SECURITY.md](docs/SECURITY.md#known-gaps) — notably: the app speaks
plain HTTP and expects TLS termination in front of it; `APP_ENV=production` is
the only value that activates production mode; and the bundled Redis client
lacks `EVAL`, so launch-token redemption uses a non-atomic `GET` + `DEL`
fallback.

[0.1.0]: https://example.com/dearlive-games/releases/tag/v0.1.0
