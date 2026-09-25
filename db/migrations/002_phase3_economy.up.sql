CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS operator (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS operator_status_idx ON operator (status);

CREATE TABLE IF NOT EXISTS platform_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key TEXT NOT NULL UNIQUE,
    value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS platform_config_updated_at_idx ON platform_config (updated_at DESC);

CREATE TABLE IF NOT EXISTS currency (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE CHECK (code IN ('USD', 'BDT', 'INR')),
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decimal_places SMALLINT NOT NULL DEFAULT 2 CHECK (decimal_places BETWEEN 0 AND 4),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    display_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS currency_enabled_order_idx ON currency (enabled, display_order);

CREATE TABLE IF NOT EXISTS locked_field (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_slug TEXT NOT NULL CHECK (game_slug IN ('teen-patti-pro', 'greedy-monkey', 'baby-king')),
    field_name TEXT NOT NULL,
    locked_by TEXT NOT NULL,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (game_slug, field_name)
);
CREATE INDEX IF NOT EXISTS locked_field_game_idx ON locked_field (game_slug);

CREATE TABLE IF NOT EXISTS superadmin_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    before_json JSONB,
    after_json JSONB,
    ip INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS superadmin_audit_created_at_idx ON superadmin_audit (created_at DESC);

CREATE TABLE IF NOT EXISTS device_session (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL UNIQUE,
    operator_id UUID NOT NULL REFERENCES operator(id) ON DELETE CASCADE,
    operator_ip INET NOT NULL,
    device_type TEXT NOT NULL,
    game_slug TEXT NOT NULL CHECK (game_slug IN ('teen-patti-pro', 'greedy-monkey', 'baby-king')),
    mode TEXT NOT NULL CHECK (mode IN ('live', 'demo')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rounds_played INTEGER NOT NULL DEFAULT 0 CHECK (rounds_played >= 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'kicked', 'ended', 'expired')),
    ended_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS device_session_status_seen_idx ON device_session (status, last_seen DESC);
CREATE INDEX IF NOT EXISTS device_session_game_mode_idx ON device_session (game_slug, mode);

CREATE TABLE IF NOT EXISTS coin_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id UUID NOT NULL REFERENCES operator(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    coin_to_currency_rate DECIMAL(18, 8) NOT NULL CHECK (coin_to_currency_rate > 0),
    min_purchase DECIMAL(18, 4) NOT NULL CHECK (min_purchase >= 0),
    max_purchase DECIMAL(18, 4) NOT NULL CHECK (max_purchase >= 0),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (operator_id)
);
CREATE INDEX IF NOT EXISTS coin_config_operator_idx ON coin_config (operator_id);

CREATE TABLE IF NOT EXISTS player (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id UUID NOT NULL REFERENCES operator(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    username TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    country TEXT,
    currency TEXT NOT NULL REFERENCES currency(code),
    vip_tier_id UUID,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'banned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ,
    UNIQUE (operator_id, external_id)
);
CREATE INDEX IF NOT EXISTS player_operator_status_idx ON player (operator_id, status);
CREATE INDEX IF NOT EXISTS player_operator_search_idx ON player (operator_id, username, email, external_id);

CREATE TABLE IF NOT EXISTS wallet (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id UUID NOT NULL REFERENCES operator(id) ON DELETE CASCADE,
    player_id UUID NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    currency TEXT NOT NULL REFERENCES currency(code),
    balance DECIMAL(18, 4) NOT NULL DEFAULT 0 CHECK (balance >= 0),
    frozen BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (operator_id, player_id, currency)
);
CREATE INDEX IF NOT EXISTS wallet_operator_idx ON wallet (operator_id);
CREATE INDEX IF NOT EXISTS wallet_player_currency_idx ON wallet (player_id, currency);

CREATE TABLE IF NOT EXISTS wallet_transaction (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_id UUID NOT NULL REFERENCES wallet(id) ON DELETE RESTRICT,
    operator_id UUID NOT NULL REFERENCES operator(id) ON DELETE RESTRICT,
    player_id UUID NOT NULL REFERENCES player(id) ON DELETE RESTRICT,
    type TEXT NOT NULL CHECK (type IN (
        'COIN_PURCHASE', 'BET_DEBIT', 'WIN_CREDIT', 'BONUS', 'ADMIN_CREDIT',
        'WITHDRAWAL_HOLD', 'WITHDRAWAL_COMPLETED', 'REFUND'
    )),
    amount DECIMAL(18, 4) NOT NULL CHECK (amount <> 0),
    balance_before DECIMAL(18, 4) NOT NULL CHECK (balance_before >= 0),
    balance_after DECIMAL(18, 4) NOT NULL CHECK (balance_after >= 0),
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    actor_id UUID REFERENCES operator(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS wallet_transaction_operator_idx ON wallet_transaction (operator_id);
CREATE INDEX IF NOT EXISTS wallet_transaction_player_idx ON wallet_transaction (operator_id, player_id);
CREATE INDEX IF NOT EXISTS wallet_transaction_created_idx ON wallet_transaction (operator_id, created_at DESC);

CREATE OR REPLACE FUNCTION forbid_wallet_transaction_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'wallet_transaction is immutable';
END;
$$;

DROP TRIGGER IF EXISTS wallet_transaction_immutable ON wallet_transaction;
CREATE TRIGGER wallet_transaction_immutable
BEFORE UPDATE OR DELETE ON wallet_transaction
FOR EACH ROW EXECUTE FUNCTION forbid_wallet_transaction_mutation();

CREATE TABLE IF NOT EXISTS player_note (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id UUID NOT NULL REFERENCES operator(id) ON DELETE CASCADE,
    player_id UUID NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    created_by UUID NOT NULL REFERENCES operator(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS player_note_operator_player_idx ON player_note (operator_id, player_id);

CREATE TABLE IF NOT EXISTS player_status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id UUID NOT NULL REFERENCES operator(id) ON DELETE CASCADE,
    player_id UUID NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    from_status TEXT NOT NULL CHECK (from_status IN ('active', 'suspended', 'banned')),
    to_status TEXT NOT NULL CHECK (to_status IN ('active', 'suspended', 'banned')),
    reason TEXT NOT NULL,
    actor_id UUID NOT NULL REFERENCES operator(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS player_status_history_operator_player_idx
    ON player_status_history (operator_id, player_id, created_at DESC);
