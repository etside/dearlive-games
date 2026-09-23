# DEPLOYMENT (staging, Vercel)

No VPS. No production. Project: `dearlive-games-staging`.

## Prereqs

- Vercel CLI logged in (`vercel whoami` → `whotjms-hue`).
- Upstash Redis (or compatible): note host/port/username/password/TLS/DB.

## Required environment variables (Vercel → Project → Settings → Environment)

| Var | Value |
|---|---|
| `APP_ENV` | `staging` |
| `REDIS_HOST` | Upstash primary endpoint host |
| `REDIS_PORT` | `6379` |
| `REDIS_USERNAME` | `default` |
| `REDIS_PASSWORD` | Upstash password (**secret, never commit**) |
| `REDIS_TLS` | `true` |
| `REDIS_DB` | `0` (isolated staging DB/instance) |
| `GAME_ADMIN_KEYS` | `op-<rand>:operator,ro-<rand>:auditor,admin-<rand>:admin,super-<rand>:superadmin` |
| `COIN_CURRENCY` | `TEST` |

Without Redis vars, `/api/*` returns 503 (static UI still loads).

## Deploy

```
vercel build          # must pass; inspects .vercel/output
vercel deploy         # preview URL; smoke-test STAGING.md flow
vercel --prod         # only for the agreed staging promotion
```

`vercel.json` maps `/api/*` → serverless `api/index.py` (10s max),
static clients + master assets served directly, no-store on `/api/*`.

## What runs where

- Static: `/` launcher, `/teen-patti-pro/`, `/greedy-lion/`,
  `/monkey-wheel/`, `/baby-king/`, `/greedy-monkey/`, master
  Lottie/GIF/WAV, SVG pack, theme/manifest JSON, `sdk/*`, `docs/*`.
- Functions: every `/api/v1/*` route via `staging/wsgi.py` reusing
  `games/teen_patti_pro/api.py` Handler logic; state in Redis
  (`stg:*` rooms/snapshots/locks/audit/config, `dearlive:*` contract
  stores); Respond 503 loudly if Redis is unreachable.
