# DearLive Integration Handoff

**Version:** v1.1.0 · **Repo:** https://github.com/etside/dearlive-games
**Client next step:** read this file top to bottom, then `README.md` for the
ten-step walkthrough.

---

## What You're Getting

Teen Patti Pro — a single, server-authoritative card game.

| | |
| --- | --- |
| Game | 3 seats, 3 cards, highest approved hand wins the pot |
| API | REST + WebSocket in one process, sharing one game state |
| Admin | Operator console at `/admin` — nine sections plus scheduling |
| Client | Canvas game client, WebView-ready, no build step |
| Assets | 93 / 93 wired, each with a procedural fallback |
| Tests | 607 passing, 2 skipped, 0 failing |
| Repo | 8.2 MB tracked; ~4 MB full clone |

There is no `superadmin` role and there never is in this release. The role
ladder is `admin > operator > auditor`.

---

## Try It Before You Build Anything

No database, no Redis, no keys:

```bash
python -m games.teen_patti_pro.api --demo
# Open http://localhost:8000/teen-patti-pro
```

Equivalent, and the same thing the README documents:

```bash
./scripts/serve-demo.sh
```

In-memory only. It refuses to start under `APP_ENV=production` and ignores a
`DATABASE_URL` left in your shell, so the demo cannot accidentally become
half-real.

Run the tests before you provision anything:

```bash
redis-server --daemonize yes   # 8 test files need this; the rest do not
python -m pytest -q
```

---

## What You Provide

**Verified against `Settings.validate_for_production()` and
`.env.example`.** The service refuses to boot in production with any of the
required values missing, rather than defaulting them.

### Infrastructure

| Variable | Example | Required in production |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db?sslmode=require` | yes |
| `REDIS_HOST` | `redis.yourdomain.com` | yes |
| `REDIS_PORT` | `6379` | yes |
| `APP_ENV` | `production` | yes |
| `CURRENCY` | `USD` | yes |
| `TOKEN_KEY_PREFIX` | `dearlive:launch:` | yes |

### Secrets you generate

| Variable | How to generate |
| --- | --- |
| `SETTLEMENT_SIGNING_SECRET` | `openssl rand -hex 32` |
| `GAME_ADMIN_KEYS` | `openssl rand -hex 32` per key, as `key:admin,key:operator,key:auditor` |
| `PROVIDER_API_KEYS` | your B2B operator credentials |
| `WALLET_API_KEY` | `openssl rand -hex 32` |
| `WALLET_CLIENT_SECRET` | `openssl rand -hex 32` |
| `DEARLIVE_API_KEY` | `openssl rand -hex 32` |
| `DEARLIVE_CLIENT_SECRET` | `openssl rand -hex 32` |
| `WEBHOOK_SECRET` | `openssl rand -hex 32` |
| `OPERATOR_TOKEN_SECRET` | `openssl rand -hex 32` — only for PIN login |
| `OPERATOR_PIN_HASH` | `bcrypt` of your PIN — only for PIN login |

PIN login is **optional and off by default**. With `OPERATOR_PIN_HASH` unset
the route answers `501` and API keys are the only path. That is deliberate.

> These do **not** exist in this release: `SUPERADMIN_TOKEN_SECRET`,
> `SUPERADMIN_PIN_HASH`, `JWT_SECRET`, `JWT_REFRESH_SECRET`,
> `SESSION_TOKEN_SECRET`. The `superadmin` role was removed before v1.0.0, so
> setting any of them would be silently ignored and might give you false
> confidence that a control is in place.

### Public addressing

| Variable | Example |
| --- | --- |
| `GAMES_BASE_URL` | `https://api.yourdomain.com` |
| `DEARLIVE_API_BASE_URL` | `https://yourdomain.com` |
| `WALLET_BASE_URL` | `https://yourdomain.com/operator` |
| `PROVIDER_PUBLIC_BASE_URL` | `https://api.yourdomain.com` |
| `SETTLEMENT_WEBHOOK_URL` | `https://yourdomain.com/webhooks/game` |

**CORS is not configured through this service.** The app sets no
`Access-Control-*` headers, so `CORS_ORIGINS` is not read here — allow the
origin in your reverse proxy instead. Do not expect setting it to change
anything.

---

## Integration Steps

1. Clone the repo.
   ```bash
   git clone https://github.com/etside/dearlive-games.git
   cd dearlive-games
   ```

2. Bootstrap dependencies and `.env`.
   ```bash
   bash scripts/setup-dev.sh
   ```

3. Create the schema.
   ```bash
   export DATABASE_URL='postgresql://...'
   bash scripts/apply-migration.sh
   ```
   Migrations are numbered 001-007 and applied in order. They are additive and
   idempotent, so re-running is safe. 001 is a single file; 002-007 each have
   `.up.sql` and `.down.sql`.

4. Fill `.env` with the variables above. See `.env.example` for the full list.

5. Start the service.
   ```bash
   python -m games.teen_patti_pro.api --host 0.0.0.0 --port 5002 --ws-port 5003
   ```
   For a Redis-backed staging adapter instead, see `docs/DEPLOYMENT.md` § B.

6. **Verify it is alive.** The health endpoint is `/health` — there is no
   `/api/v1/health`, which returns 404.
   ```bash
   curl -s http://localhost:5002/health
   ```
   Expect the standard envelope:
   ```json
   {"success":true,"code":"OK","data":{"game":"teen-patti-pro", ... }}
   ```

7. **Implement the wallet callbacks** — the only real integration work. You
   implement four endpoints; the game calls them. Exact contract:
   `docs/INTEGRATION.md`.

8. Point the frontend at `API_PUBLIC_URL` (or pass `?api=`).

9. **Visual review.** Open the game and the console at `/admin`. Appearance
   was never rendered during the build — see Limitations.

10. **Turn on real money.** Nothing accepts real bets until the rules are
    confirmed and the game is enabled:
    ```bash
    curl -X POST -H "X-Admin-Key: $ADMIN_KEY" \
      https://api.yourdomain.com/api/v1/admin/games/teen-patti-pro/enable
    ```
    There is no `ENABLE_REAL_MONEY` flag. The real gate is
    `APP_ENV=production` plus starting with `--confirmed`; while the rules are
    unconfirmed the service rejects real-money actions with
    `403 TBC_RULE_UNCONFIRMED`.

---

## Support

| Document | Covers |
| --- | --- |
| [README.md](../README.md) | Ten-step integration walkthrough, troubleshooting, API reference |
| [docs/API.md](API.md) | Endpoint reference |
| [docs/INTEGRATION.md](INTEGRATION.md) | Platform ↔ games contract, wallet callbacks, webhooks |
| [docs/ADMIN.md](ADMIN.md) | Admin keys, roles, every endpoint, scheduling |
| [docs/DEPLOYMENT.md](DEPLOYMENT.md) | Deploy, every environment variable, schema alignment |
| [docs/ARCHITECTURE.md](ARCHITECTURE.md) | Module map, data model, key decisions |
| [docs/GAME_RULES.md](GAME_RULES.md) | The ruleset, traceable to code |
| [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common failures |
| [docs/COMPLIANCE_REPORT.md](COMPLIANCE_REPORT.md) | BRD/SRS audit: status and evidence per requirement |

---

## Known Limitations

- **Visual appearance is yours to review.** All 93 assets are wired and each
  degrades to the previous procedural drawing if it fails to load, but no
  browser was available in the build environment, so nothing was rendered.
- **The reference-image comparison is outstanding.** The two reference
  screenshots were requested and were not present.
- **No production URL** until you supply infrastructure.
- **The test suite needs a local Redis** for the staging and provider-integration
  tests (8 files, ~50 tests). Everything else runs with no infrastructure.
- **Reports** in the admin console render settlement health and say so on
  screen; the SRS names a Reports section but no reports endpoint, and this
  package does not invent one.
- **Auto Bet** and a **configurable payout formula** are unimplemented; the SRS
  marks both TBC and the routes gate cleanly.
