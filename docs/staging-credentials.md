# Staging credentials policy

Staging URL: see STAGING.md (preview deployments get fresh URLs).

## What exists

- **Players:** no passwords. `POST /api/v1/staging/test-login` accepts any
  player name (`qa-player`, `qa-player-2`, `qa-player-3` suggested) and
  funds 20,000 TEST coins once per player. Anyone with the URL can play.
- **Admin keys:** four role keys (`operator`, `auditor`, `admin`,
  `superadmin`) held in the `GAME_ADMIN_KEYS` Vercel env secret
  (Production + Preview). Values are issued at setup, delivered to the
  tester out-of-band (chat), and MUST never be committed to git, docs,
  screenshots, or logs.

## Roles (superadmin > admin > operator > auditor)

| Role | Allowed |
|---|---|
| auditor | GET admin endpoints: config views, games, audit, webhooks |
| operator | + round start/close/result/settle (all games) |
| admin | general operator role (same as operator + future admin reads) |
| superadmin | + `PUT /api/v1/admin/games/{id}/config` (audited) |

Unknown/missing keys → 403. Every privileged call is audit-logged
(actor/action/before/after/timestamp/reason).

## Rotation

Generate + replace (staging project only):

```
OP="op-$(openssl rand -hex 12)" RO="ro-$(openssl rand -hex 12)"
AD="admin-$(openssl rand -hex 12)" SU="super-$(openssl rand -hex 12)"
printf '%s' "$OP:operator,$RO:auditor,$AD:admin,$SU:superadmin" \
  | vercel env add GAME_ADMIN_KEYS production
printf '%s' "$OP:operator,$RO:auditor,$AD:admin,$SU:superadmin" \
  | vercel env add GAME_ADMIN_KEYS preview
vercel deploy --yes   # pick up rotated secrets
```

Then re-issue the new values to testers and discard the old ones.

## Still required from the project owner

Upstash-compatible Redis env (Vercel → dearlive-games-staging →
Settings → Environment, Production + Preview, then redeploy):
`REDIS_HOST`, `REDIS_PORT=6379`, `REDIS_USERNAME=default`,
`REDIS_PASSWORD` (secret), `REDIS_TLS=true`, `REDIS_DB=0`.
Until set, `/api/*` returns 503 UNAVAILABLE (static UI unaffected).
