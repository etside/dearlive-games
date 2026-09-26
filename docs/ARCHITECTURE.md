# Architecture — DearLive Games (Game 1: Teen Patti Pro)

```
DearLive host app (Flutter, xyz.lrlive.app)
  │  games-icon (audio-room menu) → launch token (prod API, SAME Redis)
  ▼
WebView → {GAMES_BASE_URL}/teen-patti-pro/?session=…   (static client/, Canvas)
  │  REST :5002 (bets, state) + WS :5003 (push/ticks)
  ▼
TeenPattiService (authoritative)          ◄── JEV reviews (advisory only, offline)
  │  engine.Room (deterministic, locked) + wallet adapter + idempotency +
  │  token/session stores + audit + webhooks
  ▼
Redis (tokens/sessions/locks) + DB (rounds/bets/results/settlements/ledger)
  ▼
DearLive auth/player/room/wallet APIs  [CLIENT API REQUIRED]
```

## Module map
- `common/` — engine iface, lifecycle, envelope, idempotency, wallet iface (+memory),
  session/token iface (+memory), audit, webhooks (HMAC), **plugins** (versioned engine
  registry, fail-closed), **skills** (isolated post-commit hooks). Game-agnostic.
- `games/teen_patti_pro/` — config (versioned, TBC-flagged), engine (pure+locked),
  **table** (precomputed 22,100-hand O(1) lookup, parity-guaranteed), service (money
  order, TBC gate, sweep, skill emits), api (REST+static client+catalog), ws (RFC6455
  push), client (Canvas WebView), plugin (live 1.1.0).
- `games/greedy` (Greedy Monkey), `games/animal_wheel/` (Baby King) —
  wheel outcome engines at DearLive parity (`common/wheel.py`), plugins
  status=planned (no money paths until rules confirmed). Old ids kept as aliases.
- `admin/api.py` — RBAC matrix + audited() wrapper. `tools/jev_review.py` — review harness.

## Data model: which table is which

```
                 game  (007)  one row: teen-patti-pro
                   |  config_version
        game_configuration / game_option
                   |
        game_round (007)  round_no unique per game, seed_hex + deck_commitment
              /        \
    game_bet (007)   game_result (007)
         |                  |
         +---- game_settlement (007)  bet_id UNIQUE, state incl. settled_pending
```

The SRS names are created in `db/migrations/007_spec_tables.up.sql`. Earlier
migrations created a parallel set of names (`wallet`, `coin_config`,
`superadmin_audit`, `settlements`); the full mapping, and which one the runtime
reads today, is in [DEPLOYMENT.md](DEPLOYMENT.md#schema-alignment-legacy-names-vs-the-srs-data-model).

Two invariants are enforced by the database rather than by application code,
because application code cannot enforce them across processes or restarts:

- `game_settlement.bet_id` UNIQUE — one settlement per bet, so no double payout
  (BR-07). The legacy `settlements` table has the same constraint from 004.
- `game_bet.idempotency_key` unique where present — a retried request cannot
  produce a second bet (BR-06). Partial, so a client that sends no key does not
  collide with other such clients.

`admin_audit` is append-only: `REVOKE UPDATE, DELETE` is issued in 007. That
closes the accidental-mutation case; a role that genuinely lacks UPDATE/DELETE
is the client's to configure, and this is stated rather than implied.

## Key decisions (all JEV-reviewed)
1. Server-authoritative everything financial; client renders snapshots only.
2. Per-room mutex serializes bet-decision vs close; decision_time invariant.
3. Debit-before-create + compensate-on-any-post-debit-failure; service `_settle_lock`
   + UNIQUE settlement.bet_id + idempotent wallet ops (JEV-found races fixed).
4. Dead-heat pro-rata tie split (JEV-found fairness bug fixed); dust carried, no seat bias.
5. TBC rules versioned-config-gated; `confirmed=False` blocks real money (E_TBC_BLOCKED);
   every settlement stamps config_version.
6. Audit: seed + deck_commitment per round (reproducible results).
7. Reconnect: session_id + last_seen_seq → redacted snapshot + capped event replay.

## Ports (dev): API 5002, WS 5003. Prod: behind TLS reverse proxy; Redis + DB external.
## Scaling: rooms are independent (lock per room); sticky-by-room routing scales horizontally.
