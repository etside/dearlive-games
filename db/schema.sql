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

-- ============ Coin Packages & Orders ============

-- coin_package: purchasable coin bundles (admin-editable, no hardcoded values).
CREATE TABLE IF NOT EXISTS coin_package (
  package_id     TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  coins          BIGINT NOT NULL CHECK (coins > 0),
  price_minor    BIGINT NOT NULL CHECK (price_minor > 0),  -- minor currency units (cents)
  currency       TEXT NOT NULL DEFAULT 'USD',
  bonus_percent  INTEGER NOT NULL DEFAULT 0 CHECK (bonus_percent >= 0),
  bonus_coins    BIGINT NOT NULL DEFAULT 0,  -- coins * bonus_percent / 100
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order     INTEGER NOT NULL DEFAULT 0,
  tags           JSONB NOT NULL DEFAULT '[]',  -- ["starter","popular","value","mega"]
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_coin_package_active ON coin_package(is_active);

-- Seed 4 starter coin packages (admin-editable, not hardcoded).
-- These are DRAFT placeholders; user MUST CONFIRM before real-money launch.
INSERT INTO coin_package (package_id, name, coins, price_minor, currency, bonus_percent, bonus_coins, is_active, sort_order, tags) VALUES
  ('pkg_starter', 'Starter', 1000, 99, 'USD', 0, 0, TRUE, 1, '["starter"]'),
  ('pkg_popular', 'Popular', 5000, 499, 'USD', 10, 500, TRUE, 2, '["popular"]'),
  ('pkg_value', 'Value', 12000, 999, 'USD', 20, 2400, TRUE, 3, '["value"]'),
  ('pkg_mega', 'Mega', 30000, 1999, 'USD', 50, 15000, TRUE, 4, '["mega"]')
ON CONFLICT (package_id) DO NOTHING;

-- coin_order: purchase orders from players.
CREATE TABLE IF NOT EXISTS coin_order (
  order_id       TEXT PRIMARY KEY,
  player_id      TEXT NOT NULL REFERENCES player(player_id),
  package_id     TEXT NOT NULL REFERENCES coin_package(package_id),
  coins_base     BIGINT NOT NULL,
  coins_bonus    BIGINT NOT NULL DEFAULT 0,
  coins_total    BIGINT NOT NULL,
  price_minor    BIGINT NOT NULL,
  currency       TEXT NOT NULL DEFAULT 'USD',
  status         TEXT NOT NULL DEFAULT 'pending',  -- pending | completed | failed | refunded | cancelled
  provider       TEXT NOT NULL DEFAULT '',  -- stripe, razorpay, etc.
  provider_ref   TEXT NOT NULL DEFAULT '',  -- external payment reference
  idempotency_key TEXT NOT NULL UNIQUE,
  meta           JSONB NOT NULL DEFAULT '{}',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_coin_order_player ON coin_order(player_id);
CREATE INDEX IF NOT EXISTS idx_coin_order_status ON coin_order(status);

-- coin_event_type: enum-like reference for wallet_transaction.kind
-- (used for documentation and validation)
CREATE TABLE IF NOT EXISTS coin_event_type (
  kind       TEXT PRIMARY KEY,
  direction  TEXT NOT NULL CHECK (direction IN ('debit','credit')),  -- debit = coins leave player, credit = coins enter player
  description TEXT NOT NULL
);
-- Seed standard types:
INSERT INTO coin_event_type (kind, direction, description) VALUES
  ('COIN_ADD', 'credit', 'Admin grants coins to player'),
  ('COIN_PURCHASE', 'credit', 'Player buys coins via real money'),
  ('COIN_BET_DEBIT', 'debit', 'Bet placed, coins deducted'),
  ('COIN_WIN_CREDIT', 'credit', 'Win credited to player'),
  ('COIN_REFUND', 'credit', 'Bet cancelled, coins returned'),
  ('COIN_BONUS', 'credit', 'Daily / promo / task reward'),
  ('COIN_TEST_GRANT', 'credit', 'Dev/staging test grant only')
ON CONFLICT (kind) DO NOTHING;

-- ============ Content Management (How to Play, Rules, Guidelines) ============

-- game_content: versioned, localized content per game.
CREATE TABLE IF NOT EXISTS game_content (
  content_id    TEXT PRIMARY KEY,
  game_id       TEXT NOT NULL REFERENCES game(game_id),
  type          TEXT NOT NULL,  -- how_to_play | rules | guidelines
  locale        TEXT NOT NULL DEFAULT 'en',
  version       TEXT NOT NULL,
  title         TEXT NOT NULL,
  body          JSONB NOT NULL,  -- structured content: sections, steps, faq, etc.
  is_published  BOOLEAN NOT NULL DEFAULT FALSE,
  effective_from TIMESTAMPTZ,  -- null = immediate when published
  created_by    TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (game_id, type, locale, version)
);
CREATE INDEX IF NOT EXISTS idx_game_content_lookup ON game_content(game_id, type, locale, is_published);

-- ============ Legal / T&C ============

-- legal_document: versioned legal documents (global + per-game).
CREATE TABLE IF NOT EXISTS legal_document (
  doc_id        TEXT PRIMARY KEY,
  game_id       TEXT REFERENCES game(game_id),  -- null = global
  doc_type      TEXT NOT NULL,  -- terms | privacy | coin_purchase | responsible_gaming | refund | age_verification | jurisdiction
  version       TEXT NOT NULL,
  title         TEXT NOT NULL,
  body_md       TEXT NOT NULL,  -- markdown content
  locale        TEXT NOT NULL DEFAULT 'en',
  is_published  BOOLEAN NOT NULL DEFAULT FALSE,
  effective_from TIMESTAMPTZ,  -- null = immediate when published
  created_by    TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (game_id, doc_type, locale, version)
);
CREATE INDEX IF NOT EXISTS idx_legal_doc_lookup ON legal_document(game_id, doc_type, locale, is_published);

-- legal_acceptance: player acceptance log for version changes.
CREATE TABLE IF NOT EXISTS legal_acceptance (
  acceptance_id  TEXT PRIMARY KEY,
  player_id      TEXT NOT NULL REFERENCES player(player_id),
  doc_id         TEXT NOT NULL REFERENCES legal_document(doc_id),
  game_id        TEXT REFERENCES game(game_id),
  version        TEXT NOT NULL,
  accepted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip_hash        TEXT NOT NULL DEFAULT '',
  user_agent     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_legal_accept_player ON legal_acceptance(player_id);
CREATE INDEX IF NOT EXISTS idx_legal_accept_doc ON legal_acceptance(doc_id);

-- ============ Auto-Bet Configuration ============

-- auto_bet_config: per-player auto-bet settings per game.
CREATE TABLE IF NOT EXISTS auto_bet_config (
  config_id       TEXT PRIMARY KEY,
  player_id       TEXT NOT NULL REFERENCES player(player_id),
  game_id         TEXT NOT NULL REFERENCES game(game_id),
  option_ids      TEXT[] NOT NULL,  -- array of option_ids to bet on
  amount_per_round BIGINT NOT NULL CHECK (amount_per_round > 0),
  max_rounds       INTEGER NOT NULL DEFAULT 0 CHECK (max_rounds >= 0),  -- 0 = unlimited
  rounds_played    INTEGER NOT NULL DEFAULT 0,
  stop_on_balance_below BIGINT NOT NULL DEFAULT 0,  -- stop if balance drops below
  stop_on_loss_streak  INTEGER NOT NULL DEFAULT 0,  -- stop after N consecutive losses
  stop_on_win_streak   INTEGER NOT NULL DEFAULT 0,  -- stop after N consecutive wins
  is_active        BOOLEAN NOT NULL DEFAULT FALSE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_autobet_player ON auto_bet_config(player_id);
