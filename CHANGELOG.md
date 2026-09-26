# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-26

First shippable release. Server-authoritative Teen Patti Pro with a REST API,
a real-time WebSocket, and a B2B provider integration.

### Added

**Game**
- Teen Patti Pro engine: 3 seats (A/B/C), highest-hand wins, seat betting.
- Round lifecycle `BETTING_OPEN → CLOSED → RESULT → SETTLED`, plus a background
  sweeper (`_start_sweeper`) for timer expiry on long-lived servers.
- Configurable denomination set (20/100/500/1000) and table config via admin
  routes.
- Greedy Monkey and Baby King wheel games sharing the same adapter.

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
