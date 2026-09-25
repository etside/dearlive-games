CREATE TABLE IF NOT EXISTS demo_session (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_slug TEXT NOT NULL CHECK (game_slug IN ('teen-patti-pro', 'greedy-monkey', 'baby-king')),
    starting_balance DECIMAL(18, 4) NOT NULL CHECK (starting_balance >= 0),
    current_balance DECIMAL(18, 4) NOT NULL CHECK (current_balance >= 0),
    currency TEXT NOT NULL CHECK (currency IN ('USD', 'BDT', 'INR')),
    lang TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    ip_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed', 'expired'))
);
CREATE INDEX IF NOT EXISTS demo_session_status_expires_idx
    ON demo_session (status, expires_at);
