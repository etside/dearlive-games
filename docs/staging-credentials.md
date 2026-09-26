# Staging credentials policy

Staging URL: see STAGING.md (preview deployments get fresh URLs).

## What exists

- **Players:** no passwords. `POST /api/v1/staging/test-login` accepts any
  player name (`qa-player`, `qa-player-2`, `qa-player-3` suggested) and
  funds 20,000 TEST coins once per player. Anyone with the URL can play.
- **Admin keys:** four role keys (`operator`, `auditor`, `admin`,
  `admin`) held in the `GAME_ADMIN_KEYS` environment secret
  (Production + Preview). Values are issued at setup, delivered to the
  tester out-of-band (chat), and MUST never be committed to git, docs,
  screenshots, or logs.

## Roles (admin > operator > auditor)

| Role | Allowed |
|---|---|
| auditor | GET admin endpoints: config views, games, audit, webhooks |
| operator | + round start/close/result/settle |
| admin | + `PUT /api/v1/admin/games/{id}/config` (audited), packages, player overrides, maintenance |

There is no superadmin role: `admin` is the top role. Narrow a key to one game
with `GAME_ADMIN_SCOPES=key:teen-patti-pro` instead of escalating to a
higher-privilege credential.

Unknown/missing keys → 403. Every privileged call is audit-logged
(actor/action/before/after/timestamp/reason).

## Rotation

Generate + replace (staging project only):

Keys are held in the `GAME_ADMIN_KEYS` environment variable as
`key:role,key:role`. Roles are `admin` (top), `operator` and `auditor`;
there is no superadmin role.

```bash
OP="op-$(openssl rand -hex 12)"
RO="ro-$(openssl rand -hex 12)"
AD="admin-$(openssl rand -hex 12)"
export GAME_ADMIN_KEYS="$OP:operator,$RO:auditor,$AD:admin"

# where you run it depends on the host:
#   local / VM    -> put it in .env
#   container     -> docker compose env_file / --env-file
#   any PaaS      -> that platform's secret store
printf '%s' "$GAME_ADMIN_KEYS"
```

Restart the server after rotating; keys are read once at import.

Then re-issue the new values to testers and discard the old ones.

## Still required from the project owner

Upstash-compatible Redis env (set it in whatever secret store your host uses →
Settings → Environment, Production + Preview, then redeploy):
`REDIS_HOST`, `REDIS_PORT=6379`, `REDIS_USERNAME=default`,
`REDIS_PASSWORD` (secret), `REDIS_TLS=true`, `REDIS_DB=0`.
Until set, `/api/*` returns 503 UNAVAILABLE (static UI unaffected).
