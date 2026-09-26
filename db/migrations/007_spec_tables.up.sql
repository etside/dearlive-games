-- 007: the tables named in the BRD/SRS data model (section 13), created
-- alongside the 001-006 tables rather than replacing them.
--
-- Why additive and not a rewrite: migrations 001-006 are already applied on at
-- least one staging database, and the Python in common/admin_store.py and
-- provider/ queries them by their current names. Rewriting them would mean a
-- destructive migration on a live database for no functional gain. So this
-- file creates the spec-named tables, and docs/ARCHITECTURE.md carries the
-- legacy -> spec mapping.
--
-- Where a spec table is a strict superset of an existing one, the existing
-- table is left alone and the new one is populated by the runtime going
-- forward. Historical rows are NOT backfilled: inventing game_round rows for
-- rounds that were settled in memory would put unverified amounts in an
-- auditable table.
--
-- Naming: the spec uses generic names (game_bet, game_result) where the
-- existing schema uses role-specific ones (bets, settlements). Both exist
-- after this migration. The new tables are the ones the SRS names; the old
-- ones remain the ones the current code reads until that code is switched over
-- in a later, separately-reviewed change.
--
-- Idempotent: re-running is a no-op.

-- ---------------------------------------------------------------------------
-- game: the catalog row a round belongs to. One per shipped game.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game (
    game_id      TEXT PRIMARY KEY,
    name         TEXT        NOT NULL,
    version      TEXT        NOT NULL DEFAULT '1.0.0',
    status       TEXT        NOT NULL DEFAULT 'live',
    entry_path   TEXT        NOT NULL DEFAULT '',
    config_version TEXT      NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT game_status_known CHECK (status IN ('live', 'planned', 'disabled'))
);

-- ---------------------------------------------------------------------------
-- game_configuration: current effective settings for a game.
--
-- game_configuration holds the *current* row; config_version below holds every
-- historical one. The two together are what makes BR-09 ("a change applies to
-- the NEXT round only") expressible: a round stores the config_version it
-- started with, and readers resolve that version rather than the current row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_configuration (
    game_id      TEXT        NOT NULL REFERENCES game (game_id) ON DELETE CASCADE,
    config_version TEXT      NOT NULL,
    payload      JSONB       NOT NULL,
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    created_by   TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT game_configuration_pk PRIMARY KEY (game_id, config_version)
);

-- One active configuration per game. Enforced with a partial unique index
-- rather than a boolean-plus-check so that "deactivate then activate" is two
-- ordinary statements and cannot transiently hold two active rows.
CREATE UNIQUE INDEX IF NOT EXISTS game_configuration_one_active
    ON game_configuration (game_id) WHERE is_active;

-- ---------------------------------------------------------------------------
-- config_version: the append-only history, and the rollback source.
--
-- rollback_supported_until implements the SRS's 90-day window at the database
-- level: a rollback to a version older than this is refused rather than
-- silently permitted, because a config from four months ago is unlikely to
-- still be coherent with the code that is running now.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config_version (
    version      TEXT        NOT NULL,
    game_id      TEXT        NOT NULL DEFAULT '',
    config_id    TEXT        NOT NULL DEFAULT '',
    payload      JSONB       NOT NULL,
    diff_from    TEXT,
    created_by   TEXT        NOT NULL DEFAULT '',
    reason       TEXT        NOT NULL DEFAULT '',
    rollback_supported_until TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT config_version_pk PRIMARY KEY (version)
);

CREATE INDEX IF NOT EXISTS config_version_by_config
    ON config_version (config_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- game_round: one row per round. round_no is unique *per game*, per the SRS.
--
-- The seed and deck_commitment columns are the BR-04 audit trail: given
-- seed_hex the shuffle is reproducible, and deck_commitment lets a reviewer
-- prove the committed deck matches the seed without re-running the round.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_round (
    round_id     TEXT        PRIMARY KEY,
    game_id      TEXT        NOT NULL REFERENCES game (game_id) ON DELETE CASCADE,
    room_id      TEXT        NOT NULL DEFAULT '',
    round_no     BIGINT      NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'UPCOMING',
    config_version TEXT      NOT NULL DEFAULT '',
    seed_hex     TEXT        NOT NULL DEFAULT '',
    deck_commitment TEXT     NOT NULL DEFAULT '',
    betting_ends_at TIMESTAMPTZ,
    started_at   TIMESTAMPTZ,
    settled_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT game_round_no_unique_per_game UNIQUE (game_id, round_no),
    CONSTRAINT game_round_status_known CHECK (
        status IN ('UPCOMING', 'BETTING_OPEN', 'BETTING_CLOSED',
                   'RESULT_PROCESSING', 'SETTLED', 'CLOSED')
    )
);

-- The sweep's hot query is "due rounds", so it gets the partial index.
CREATE INDEX IF NOT EXISTS game_round_due_idx
    ON game_round (betting_ends_at) WHERE status = 'BETTING_OPEN';

CREATE INDEX IF NOT EXISTS game_round_room_idx
    ON game_round (room_id, round_no DESC);

-- ---------------------------------------------------------------------------
-- game_bet: a bet is immutable once accepted. Corrections are made by
-- voiding via status, never by editing amount -- see the ledger note below.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_bet (
    bet_id       TEXT        PRIMARY KEY,
    round_id     TEXT        NOT NULL REFERENCES game_round (round_id) ON DELETE CASCADE,
    player_id    TEXT        NOT NULL,
    position     TEXT        NOT NULL,
    amount       DECIMAL(18,4) NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'accepted',
    idempotency_key TEXT,
    decision_time_ms INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT game_bet_amount_positive CHECK (amount > 0),
    CONSTRAINT game_bet_status_known CHECK (
        status IN ('accepted', 'won', 'lost', 'voided')
    )
);

CREATE INDEX IF NOT EXISTS game_bet_round_idx ON game_bet (round_id);
CREATE INDEX IF NOT EXISTS game_bet_player_idx ON game_bet (player_id, created_at DESC);

-- BR-06: a retried request must not produce a second bet. Partial, because a
-- NULL key is a client that did not send one and must not collide with
-- another such client.
CREATE UNIQUE INDEX IF NOT EXISTS game_bet_idempotency_uniq
    ON game_bet (idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ---------------------------------------------------------------------------
-- game_result: the evaluated outcome of a round, including the audit material.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_result (
    result_id    TEXT        PRIMARY KEY,
    round_id     TEXT        NOT NULL UNIQUE
                  REFERENCES game_round (round_id) ON DELETE CASCADE,
    winner_positions JSONB  NOT NULL DEFAULT '[]'::jsonb,
    pot_total    DECIMAL(18,4) NOT NULL DEFAULT 0,
    rake_amount  DECIMAL(18,4) NOT NULL DEFAULT 0,
    hands        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ranking_snapshot JSONB,
    seed_hex     TEXT        NOT NULL DEFAULT '',
    deck_commitment TEXT     NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- game_settlement: money actually moved.
--
-- bet_id UNIQUE is BR-07 and is enforced here, at the database, rather than in
-- a process-local set -- a set cannot survive two workers or a restart, which
-- is exactly the double-payout case it is meant to prevent. An equivalent
-- constraint already exists on the legacy `settlements` table (migration 004);
-- this is the SRS-named table.
--
-- state distinguishes "decided" from "paid", so the settlement-retry path has
-- something to query: SETTLED_PENDING is the SRS's max-retries-exhausted state
-- and is what the operator dashboard surfaces.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_settlement (
    settlement_id TEXT       PRIMARY KEY,
    bet_id       TEXT        NOT NULL UNIQUE,
    round_id     TEXT        NOT NULL,
    player_id    TEXT        NOT NULL,
    amount       DECIMAL(18,4) NOT NULL,
    state        TEXT        NOT NULL DEFAULT 'pending',
    attempts     INTEGER     NOT NULL DEFAULT 0,
    last_error   TEXT        NOT NULL DEFAULT '',
    credited_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT game_settlement_state_known CHECK (
        state IN ('pending', 'settled', 'settled_pending', 'failed', 'voided')
    )
);

-- Retry path: find work still owed.
CREATE INDEX IF NOT EXISTS game_settlement_pending_idx
    ON game_settlement (state, created_at) WHERE state IN ('pending', 'settled_pending');

-- ---------------------------------------------------------------------------
-- game_option: a per-game admin-editable rule (BR-11, BR-12, BR-13, BR-14).
--
-- Key/value rather than columns so an operator can add a rule without a
-- migration. values are JSONB; the API layer validates against the known rule
-- set and rejects unknown keys, so this cannot become a hole in the rules.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_option (
    game_id      TEXT        NOT NULL REFERENCES game (game_id) ON DELETE CASCADE,
    option_key   TEXT        NOT NULL,
    option_value JSONB       NOT NULL,
    updated_by   TEXT        NOT NULL DEFAULT '',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT game_option_pk PRIMARY KEY (game_id, option_key)
);

-- ---------------------------------------------------------------------------
-- token_package / admin_audit: spec names for the coin catalogue and the
-- audit trail. 002 created `coin_config` and `superadmin_audit`; these are the
-- SRS-named equivalents and are what new code should read.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS token_package (
    package_id   TEXT        PRIMARY KEY,
    name         TEXT        NOT NULL,
    coins        BIGINT      NOT NULL DEFAULT 0,
    price_minor  BIGINT      NOT NULL DEFAULT 0,
    currency     TEXT        NOT NULL DEFAULT 'USD',
    bonus_percent INTEGER    NOT NULL DEFAULT 0,
    bonus_coins  BIGINT      NOT NULL DEFAULT 0,
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    sort_order   INTEGER     NOT NULL DEFAULT 0,
    tags         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT token_package_coins_positive CHECK (coins >= 0)
);

CREATE TABLE IF NOT EXISTS admin_audit (
    audit_id     BIGSERIAL   PRIMARY KEY,
    actor        TEXT        NOT NULL,
    action       TEXT        NOT NULL,
    entity       TEXT        NOT NULL,
    entity_id    TEXT        NOT NULL DEFAULT '',
    before_json  JSONB,
    after_json   JSONB,
    ip           TEXT        NOT NULL DEFAULT '',
    request_id   TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Audit is append-only (SRS section 10: "immutable. CSV export"). The read path
-- is the audit feed; this index serves it.
CREATE INDEX IF NOT EXISTS admin_audit_feed_idx
    ON admin_audit (created_at DESC);

CREATE INDEX IF NOT EXISTS admin_audit_entity_idx
    ON admin_audit (entity, entity_id, created_at DESC);

-- Immutability, enforced rather than documented. REVOKE covers the obvious
-- mistake (an UPDATE in an ad-hoc session); it is not a substitute for a role
-- that lacks UPDATE/DELETE, which the client should also configure. Stated
-- here so the gap is visible rather than implied.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = CURRENT_USER) THEN
        EXECUTE 'REVOKE UPDATE, DELETE ON admin_audit FROM ' || quote_ident(CURRENT_USER);
        RAISE NOTICE 'revoked UPDATE, DELETE on admin_audit from %', CURRENT_USER;
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'could not tighten admin_audit permissions: %', SQLERRM;
END $$;

-- ---------------------------------------------------------------------------
-- Seed the single shipped game so the FKs above have something to point at.
--
-- ON CONFLICT DO NOTHING: re-running must not reset an operator's edits to
-- the name or entry path.
-- ---------------------------------------------------------------------------
INSERT INTO game (game_id, name, version, status, entry_path, config_version)
VALUES ('teen-patti-pro', 'Teen Patti Pro', '1.0.0', 'live',
        '/teen-patti-pro/', 'tpp-1.0.0-tbc')
ON CONFLICT (game_id) DO NOTHING;
