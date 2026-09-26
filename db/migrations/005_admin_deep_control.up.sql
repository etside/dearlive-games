-- Admin deep-control tables: profit/risk, per-player overrides, VIP tiers,
-- and the withdrawal queue.
--
-- Four concepts from the admin specification had no home in the schema, while
-- the other four mapped cleanly onto tables that already exist:
--
--   token_package  -> coin_package          (already present in schema.sql)
--   game_config    -> game_configuration    (already versioned: PK game_id+version)
--   config_version -> version column        (no separate table needed)
--   admin_audit    -> audit_log             (actor/action/entity/before/after JSONB)
--
-- So this migration adds only what is genuinely missing, rather than creating
-- eight new tables and splitting audit three ways.
--
-- Every range from the specification is enforced as a CHECK constraint so an
-- out-of-range value is rejected by the database, not merely by a form. The
-- API re-validates for a friendly error message, but this is the backstop that
-- holds when a script or psql session writes directly.
--
-- Idempotent: re-running is a no-op.

-- ---------------------------------------------------------------------------
-- profit_risk_config
--
-- Versioned, with exactly one active row. Writes never mutate an existing row;
-- they insert the next version and flip `active`, so "apply to NEXT round only"
-- is a property of the data model rather than application discipline.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS profit_risk_config (
    config_id                 TEXT PRIMARY KEY,
    version                   INTEGER     NOT NULL,
    base_house_edge_pct       NUMERIC(5,2) NOT NULL DEFAULT 8.00,
    vip_profit_adj_pct        NUMERIC(5,2) NOT NULL DEFAULT 1.50,
    max_payout_per_round      BIGINT      NOT NULL DEFAULT 10000,
    rng_weight                NUMERIC(4,2) NOT NULL DEFAULT 1.00,
    max_daily_loss_per_player BIGINT      NOT NULL DEFAULT 500,
    active                    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_by                TEXT        NOT NULL DEFAULT '',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT profit_risk_version_positive CHECK (version >= 1),
    CONSTRAINT profit_risk_house_edge_range
        CHECK (base_house_edge_pct >= 0 AND base_house_edge_pct <= 30),
    CONSTRAINT profit_risk_vip_adj_range
        CHECK (vip_profit_adj_pct >= 0 AND vip_profit_adj_pct <= 10),
    CONSTRAINT profit_risk_max_payout_range
        CHECK (max_payout_per_round >= 100 AND max_payout_per_round <= 1000000),
    CONSTRAINT profit_risk_rng_weight_range
        CHECK (rng_weight >= 0.1 AND rng_weight <= 3.0),
    CONSTRAINT profit_risk_daily_loss_range
        CHECK (max_daily_loss_per_player >= 0 AND max_daily_loss_per_player <= 10000)
);

-- At most one active configuration. A plain UNIQUE(version) would also be
-- useful, but version is only meaningful per config_id.
CREATE UNIQUE INDEX IF NOT EXISTS profit_risk_config_version_uniq
    ON profit_risk_config (config_id, version);

-- The engine reads "the" active config on every bet, so this is a hot lookup.
CREATE UNIQUE INDEX IF NOT EXISTS profit_risk_config_single_active
    ON profit_risk_config (config_id) WHERE active;

-- ---------------------------------------------------------------------------
-- player_override
--
-- Per-player deviation from the global rules. NULL means "inherit global",
-- which is why house_edge_pct and custom_loss_limit are nullable rather than
-- defaulting to 0: 0 is a legal house edge, so it cannot mean "unset".
-- expires_at NULL means permanent; a revoked override is kept for audit
-- rather than deleted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player_override (
    override_id      TEXT PRIMARY KEY,
    player_id        TEXT NOT NULL,
    house_edge_pct   NUMERIC(5,2),
    token_delta      BIGINT      NOT NULL DEFAULT 0,
    custom_loss_limit BIGINT,
    expires_at       TIMESTAMPTZ,
    reason           TEXT        NOT NULL,
    actor            TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at       TIMESTAMPTZ,
    revoked_by       TEXT,

    CONSTRAINT player_override_edge_range
        CHECK (house_edge_pct IS NULL
               OR (house_edge_pct >= 0 AND house_edge_pct <= 30)),
    CONSTRAINT player_override_loss_limit_range
        CHECK (custom_loss_limit IS NULL
               OR (custom_loss_limit >= 0 AND custom_loss_limit <= 100000)),
    CONSTRAINT player_override_reason_required CHECK (length(btrim(reason)) > 0)
);

-- Resolution order on every bet: newest live override for this player.
CREATE INDEX IF NOT EXISTS player_override_player_live_idx
    ON player_override (player_id, created_at DESC) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- vip_tier
--
-- A player carries a `vip_tier` label today (staging/economy.py) with no
-- table behind it, so the tier's effect on house edge was unbacked. `rank`
-- makes tier ordering explicit and total.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vip_tier (
    tier_id            TEXT PRIMARY KEY,
    name               TEXT        NOT NULL UNIQUE,
    rank               INTEGER     NOT NULL,
    house_edge_adj_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    perks              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT vip_tier_rank_positive CHECK (rank >= 0),
    CONSTRAINT vip_tier_adj_range
        CHECK (house_edge_adj_pct >= -30 AND house_edge_adj_pct <= 30)
);

CREATE UNIQUE INDEX IF NOT EXISTS vip_tier_rank_uniq ON vip_tier (rank);

-- ---------------------------------------------------------------------------
-- withdrawal_request
--
-- The queue behind the admin withdrawals page. Status is constrained so a
-- typo cannot invent a state the payout job does not know how to process.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS withdrawal_request (
    request_id TEXT PRIMARY KEY,
    player_id  TEXT        NOT NULL,
    amount     BIGINT      NOT NULL,
    currency   TEXT        NOT NULL DEFAULT 'COIN',
    status     TEXT        NOT NULL DEFAULT 'PENDING',
    reason     TEXT,
    actor      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,

    CONSTRAINT withdrawal_amount_positive CHECK (amount > 0),
    CONSTRAINT withdrawal_status_known CHECK (
        status IN ('PENDING', 'APPROVED', 'REJECTED', 'PAID', 'CANCELLED')
    )
);

-- The queue is read as "everything still waiting", in age order.
CREATE INDEX IF NOT EXISTS withdrawal_request_pending_idx
    ON withdrawal_request (created_at) WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS withdrawal_request_player_idx
    ON withdrawal_request (player_id, created_at DESC);
