CREATE TABLE IF NOT EXISTS operator_access_log (
    id UUID PRIMARY KEY,
    operator_id TEXT NOT NULL DEFAULT 'global',
    operator_ip VARCHAR(128) NOT NULL,
    device_ua VARCHAR(512) NOT NULL DEFAULT '',
    device_type VARCHAR(16) NOT NULL DEFAULT 'unknown',
    country VARCHAR(8) NOT NULL DEFAULT '',
    pin_status VARCHAR(16) NOT NULL CHECK (pin_status IN ('success', 'fail', 'locked')),
    token_expires_at BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS operator_access_log_ip_created_at_idx
    ON operator_access_log (operator_ip, created_at DESC);
