-- DearLive Games — game-side Postgres schema (records ONLY).
-- DearLive owns users + wallet balances. This DB NEVER stores balances;
-- money movement lives in DearLive's ledger; here we keep txn refs +
-- game records for audit/replay. Amounts are INTEGER minor coin units.
-- Idempotency: UNIQUE(idempotency_key). Settlement: UNIQUE(bet_id).
-- Apply: psql "$DATABASE_URL" -f db/schema.sql  (or migration 001).
--
-- Logical-model map (BRD entities): player, wallet_account,
-- wallet_transaction, game, game_configuration, game_round (=rounds),
-- game_option, game_bet (=bets), game_result, game_settlement
-- (=settlements), game_history, audit_log (+ Teen Patti: game_table,
-- game_player_position, card_hand).

CREATE TABLE IF NOT EXISTS rounds (
  round_id       TEXT PRIMARY KEY,
  room_id        TEXT NOT NULL,
  round_no       INTEGER NOT NULL,
  status         TEXT NOT NULL,
  created_at_ms  BIGINT NOT NULL,
  betting_end_at_ms BIGINT NOT NULL,
  seed_hex       TEXT NOT NULL,
  deck_commit    TEXT NOT NULL,
  winner_positions TEXT NOT NULL DEFAULT '[]',
  carry_in       BIGINT NOT NULL DEFAULT 0,
  carry_out      BIGINT NOT NULL DEFAULT 0,
  config_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rounds_room ON rounds(room_id);

CREATE TABLE IF NOT EXISTS bets (
  bet_id          TEXT PRIMARY KEY,
  round_id        TEXT NOT NULL REFERENCES rounds(round_id),
  player_id       TEXT NOT NULL,
  position        TEXT NOT NULL,
  amount          BIGINT NOT NULL CHECK (amount > 0),
  decision_time_ms BIGINT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status          TEXT NOT NULL DEFAULT 'accepted',
  debit_txn_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bets_round ON bets(round_id);
CREATE INDEX IF NOT EXISTS idx_bets_player ON bets(player_id);

CREATE TABLE IF NOT EXISTS settlements (
  settlement_id  TEXT PRIMARY KEY,
  bet_id         TEXT NOT NULL UNIQUE REFERENCES bets(bet_id),
  player_id      TEXT NOT NULL,
  payout         BIGINT NOT NULL CHECK (payout >= 0),
  credit_txn_id  TEXT NOT NULL DEFAULT '',
  config_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  key          TEXT PRIMARY KEY,
  payload_hash TEXT NOT NULL,
  result       JSONB,
  expires_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_outbox (
  delivery_id  TEXT PRIMARY KEY,
  event_id     TEXT NOT NULL,
  kind         TEXT NOT NULL,
  destination  TEXT NOT NULL,
  payload      JSONB NOT NULL,
  signature    TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'queued',
  attempts     INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(event_id, destination)
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON webhook_outbox(status);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id    TEXT PRIMARY KEY,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  entity      TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  before_data JSONB,
  after_data  JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, entity_id);

-- ============ BRD logical model (all games) ============

-- player: identity mirror (DearLive is authoritative; game caches ids only).
CREATE TABLE IF NOT EXISTS player (
  player_id   TEXT PRIMARY KEY,
  game_id     TEXT NOT NULL DEFAULT '',
  room_id     TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- wallet_account: per-player currency rail reference (NO balance column —
-- balances live in DearLive; this row only keys wallet_transaction refs).
CREATE TABLE IF NOT EXISTS wallet_account (
  player_id  TEXT PRIMARY KEY REFERENCES player(player_id),
  currency   TEXT NOT NULL DEFAULT 'COIN'
);

-- wallet_transaction: immutable posted money refs. NEVER UPDATE/DELETE:
-- corrections are compensating rows (reversal_of -> original txn).
CREATE TABLE IF NOT EXISTS wallet_transaction (
  txn_id        TEXT PRIMARY KEY,
  player_id     TEXT NOT NULL REFERENCES player(player_id),
  kind          TEXT NOT NULL,  -- debit | credit | refund | void
  amount        BIGINT NOT NULL,
  ref           TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  reversal_of   TEXT REFERENCES wallet_transaction(txn_id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wtxn_player ON wallet_transaction(player_id);
CREATE OR REPLACE FUNCTION forbid_wallet_txn_update() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'wallet_transaction is immutable; post a compensating row'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_wallet_txn_immutable ON wallet_transaction;
CREATE TRIGGER trg_wallet_txn_immutable BEFORE UPDATE OR DELETE ON wallet_transaction
  FOR EACH ROW EXECUTE FUNCTION forbid_wallet_txn_update();

-- game: catalog entry (one row per game_id incl. aliases' canonical).
CREATE TABLE IF NOT EXISTS game (
  game_id       TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  dearlive_code TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'planned',  -- live | planned | disabled
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  entry         TEXT NOT NULL DEFAULT ''
);

-- game_configuration: versioned/auditable config. New version per change;
-- game rows point at current_version. TBC flags carried per version.
CREATE TABLE IF NOT EXISTS game_configuration (
  game_id      TEXT NOT NULL REFERENCES game(game_id),
  version      TEXT NOT NULL,
  confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
  tbc          JSONB NOT NULL DEFAULT '[]',
  payload      JSONB NOT NULL,  -- denoms, min/max, durations, options, multipliers, HOT, packages, localization
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (game_id, version)
);

-- game_option: configured betting options/images/multipliers/HOT flags.
CREATE TABLE IF NOT EXISTS game_option (
  game_id      TEXT NOT NULL REFERENCES game(game_id),
  option_id    TEXT NOT NULL,
  name         TEXT NOT NULL,
  weight       DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (weight > 0),
  multiplier   DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (multiplier > 0),
  icon         TEXT NOT NULL DEFAULT '',
  color_hex    TEXT NOT NULL DEFAULT '#ffffff',
  hot          BOOLEAN NOT NULL DEFAULT FALSE,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY (game_id, option_id)
);

-- game_result: authoritative server-side result per round (auditable,
-- reproducible: seed refs + outcome payload).
CREATE TABLE IF NOT EXISTS game_result (
  round_id       TEXT PRIMARY KEY REFERENCES rounds(round_id),
  game_id        TEXT NOT NULL REFERENCES game(game_id),
  winner_ref     TEXT NOT NULL DEFAULT '',  -- winning option_id / positions CSV
  outcome        JSONB NOT NULL,  -- hands / angle / multiplier / seeds
  seed_ref       TEXT NOT NULL DEFAULT '',
  config_version TEXT NOT NULL,
  published_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- game_history: player/admin-visible round/bet/result/settlement trail.
CREATE TABLE IF NOT EXISTS game_history (
  seq        BIGSERIAL PRIMARY KEY,
  game_id    TEXT NOT NULL REFERENCES game(game_id),
  round_id   TEXT NOT NULL REFERENCES rounds(round_id),
  player_id  TEXT NOT NULL,
  kind       TEXT NOT NULL,  -- bet | result | settlement | refund | autobet
  ref_id     TEXT NOT NULL,  -- bet_id / settlement_id / round_id
  summary    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ghist_player ON game_history(player_id);
CREATE INDEX IF NOT EXISTS idx_ghist_round ON game_history(round_id);

-- ============ Teen Patti Pro entities (G3) ============

-- game_table: tableId == room; players + pots live here per round.
CREATE TABLE IF NOT EXISTS game_table (
  table_id   TEXT PRIMARY KEY,
  game_id    TEXT NOT NULL REFERENCES game(game_id) DEFAULT 'teen-patti-pro',
  room_id    TEXT NOT NULL,
  round_id   TEXT REFERENCES rounds(round_id)
);

-- game_player_position: per-position pot/contribution + own contribution.
CREATE TABLE IF NOT EXISTS game_player_position (
  round_id        TEXT NOT NULL REFERENCES rounds(round_id),
  position        TEXT NOT NULL,  -- A | B | C
  pot             BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (round_id, position)
);

-- card_hand: dealt cards per position (server-side, auditable).
CREATE TABLE IF NOT EXISTS card_hand (
  round_id   TEXT NOT NULL REFERENCES rounds(round_id),
  position   TEXT NOT NULL,
  cards      JSONB NOT NULL,  -- ["14S","12H",...] face codes; hidden pre-RESULT by API
  PRIMARY KEY (round_id, position)
);
