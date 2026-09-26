# Security

## Threat model

The asset is the **wallet**, not the cards. Outcomes are server-side, so a
cheating client cannot invent a win; what it *can* try is to move money without
paying, replay a request, or impersonate an operator.

| Adversary | Capability assumed | Primary defence |
|---|---|---|
| Player (authenticated) | Full client control, can replay/reorder any request | server-authoritative engine, idempotency keys, session binding |
| Player (unauthenticated) | Can call any endpoint | every route requires a bearer session or a signed operator request |
| Operator key holder | Valid HMAC key for one game | per-game scopes, rate limiting, replay window |
| Network attacker | Sees/intercepts traffic | **TLS is mandatory in production** — see below |
| Attacker with repo access | Source code | proprietary licence; **no secret is ever committed** |

## Request signing (B2B provider API)

Every server-to-server call carries four headers:

```
X-API-Key     public key id, never the secret
X-Timestamp   unix seconds, must sit inside the freshness window
X-Nonce       unique per request, rejected on replay
X-Signature   hex(HMAC-SHA256(secret, canonical))

canonical = METHOD \n PATH \n TIMESTAMP \n NONCE \n SHA256(raw_body)
```

Enforcement lives in `provider/auth.py`. Properties that matter:

- **Signature first.** Replay is only checked *after* the signature verifies,
  so an unsigned attacker cannot burn a legitimate nonce.
- **Freshness window** rejects stale timestamps.
- **Nonce store** rejects reuse (`pvdr:nonce:<key>:<nonce>` in Redis).
- **Rate limiting** per key id.
- Use `provider.auth.sign_request()` to sign; never hand-roll it.

## Wallet safety

- `debit`, `credit`, and `rollback` are **idempotent** under
  `Idempotency-Key`. A retried debit must not double-charge.
- Settlement writes a ledger row; a settlement DLQ is exposed via
  `webhook pending()`.
- The engine debits the **real** wallet adapter. A fabricated or client-supplied
  balance is never trusted — a request with an unfunded wallet gets
  `402 INSUFFICIENT_BALANCE`.

## Production gate

`APP_ENV=production` **refuses to boot** unless all nine of these are present
and `--confirmed` is passed:

```
admin_keys  database_url  dearlive_api_base_url  games_base_url
provider_api_keys  provider_public_base_url  settlement_signing_secret
settlement_webhook_url  wallet_base_url
```

This is the real-money gate. A refusal names exactly what is missing.

`/demo/session` is disabled under production and returns 404.

## Secrets handling

- `.env` is git-ignored; only `.env.example` (placeholders) is committed.
- Never commit `*.pem`, `*.key`, `certs/`, or a populated `.env.*`.
- Never place a secret in `apps/*/dist` or the WebView bundle — a WebView can be
  unpacked.
- Rotate provider keys by changing `PROVIDER_API_KEYS` and restarting; the
  loader takes `key_id:secret` pairs.
- `settlement_signing_secret` signs outbound webhooks. Treat a leak as a
  settlement-integrity incident.

## Known gaps

Honest list, not a marketing page:

1. **No TLS in the app.** The server speaks plain HTTP and trusts a reverse
   proxy for TLS. Never expose 5002/5003 directly to the internet.
2. **`APP_ENV` matching is exact.** Production mode activates only on the exact
   string `production` (case-insensitive, whitespace-trimmed). A typo such as
   `prod` leaves the app in non-production mode. Set it explicitly and treat a
   wrong value as a failed deploy.
3. **In-memory stores lose state.** The demo and default wiring keep wallet,
   token, and session state in process memory. Restarting drops it, and running
   two app processes splits the game state. Use the Redis/Postgres stores for
   anything real.
4. **Demo and staging routes exist in non-production.** `/demo/session` and
   `/api/v1/staging/*` are gated on `APP_ENV`; confirm the value before
   exposing a host.
5. **The bundled Redis client does not support `EVAL`.** Launch-token redemption
   uses a Lua GETDEL and falls back to `GET` + `DEL`. That fallback is correct
   but not atomic — under real concurrency, prefer a client with `EVAL` support
   or a `GETDEL` equivalent.

## Reporting

Do not open a public issue for a vulnerability. Contact the maintainers
directly (see `LICENSE`).
