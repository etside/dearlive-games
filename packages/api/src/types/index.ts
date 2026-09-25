/**
 * Core type definitions for the DearLive Games API Provider
 */

// Game identification
export type GameCode = 'teen_patti_pro' | 'greedy_monkey' | 'baby_king';
export type GameKind = 'table_game' | 'wheel';

export interface GameInfo {
  game_code: string;
  name: string;
  status: 'live' | 'planned' | 'maintenance';
  kind: GameKind;
  engine_id: string;
  currencies: string[];
  min_bet: number;
  max_bet: number;
  max_players: number;
  tables: string[];
  actions: string[];
  choice_field: string;
  choices_url: string;
  launch_path: string;
  realtime: {
    protocol: 'websocket';
    path: string;
    auth: string;
  };
}

export interface GameTable {
  table_id: string;
  label: string;
  min_bet: number;
  max_bet: number;
  max_players: number;
  currency: string;
  enabled: boolean;
  live: {
    round_id: string | null;
    status: string;
    betting_end_at: number | null;
    players: number;
  };
}

// Authentication & Authorization
export type UserRole = 'superadmin' | 'admin' | 'auditor' | 'operator' | 'player';

export interface APIKey {
  key_id: string;
  key_secret: string;
  role: 'superadmin' | 'admin' | 'operator';
  scopes: string[];
  games: string[]; // empty = all games
  created_at: string;
  expires_at?: string;
  revoked: boolean;
}

export interface JWTPayload {
  sub: string;           // API key ID
  role: UserRole;
  scopes: string[];
  games: string[];
  iat: number;
  exp: number;
}

// Session & Player
export interface PlayerSession {
  session_id: string;
  session_token: string; // gst_...
  player_id: string;
  game_code: string;
  table_id: string;
  currency: string;
  language: string;
  platform: string;
  expires_at: string;
  created_at: string;
}

export interface LaunchToken {
  token: string;
  player_id: string;
  room_id: string;
  game_id: string;
  expires_at_ms: number;
  nonce: string;
}

// Wallet & Transactions
export type Currency = 'COIN' | 'USD' | 'EUR' | 'USDT';

export interface Balance {
  player_id: string;
  available: number;
  currency: Currency;
}

export interface WalletTransaction {
  txn_id: string;
  type: 'debit' | 'credit' | 'rollback';
  status: 'SUCCESS' | 'ROLLED_BACK';
  player_id: string;
  amount: number;
  currency: Currency;
  reference: string;
  idempotency_key: string;
  game_code: string;
  round_id: string;
  original_reference?: string;
  reverses_txn_id?: string;
  reason?: string;
  actor: string;
  created_at: number;
}

export interface WalletTransactionListResponse {
  player_id: string;
  count: number;
  transactions: Array<{
    txn_id: string;
    type: 'debit' | 'credit' | 'rollback';
    status: 'SUCCESS' | 'ROLLED_BACK';
    player_id: string;
    amount: number;
    currency: string;
    reference: string;
    idempotency_key: string;
    game_code: string;
    round_id: string;
    created_at: number;
  }>;
}

// Game Actions
export interface BetAction {
  action: 'bet';
  position?: string;      // Teen Patti: 'A' | 'B' | 'C'
  option_id?: string;     // Wheel: option_id
  amount: number;
  idempotency_key: string;
}

export type GameAction = BetAction;

export interface ActionResult {
  table_id: string;
  player_id: string;
  action: 'bet';
  accepted: boolean;
  bet_id: string;
  round_id: string;
  position?: string;
  option_id?: string;
  amount: number;
  decision_time: number;
}

// Game State
export interface GameState {
  table_id: string;
  player_id: string;
  state: {
    room_id: string;
    round_id: string | null;
    round_no: number;
    status: string;
    serverTime: number;
    betting_end_at: number;
    pots: Record<string, number>;
    pot_total: number;
    my_bet: number;
    carry_in: number;
    hands: Record<string, string[]>;
    raw_hands?: Record<string, string[]>;
    winners: string[];
    config_version: string;
  };
}

export interface RoundHistory {
  round_id: string;
  round_no: number;
  config_version: string;
  winners: string[];
  pot: number;
  settlements: Array<{
    settlement_id: string;
    bet_id: string;
    player_id: string;
    payout: number;
    config_version: string;
  }>;
  carry_out: number;
  settled_at: number;
}

export interface Settlement {
  settlement_id: string;
  bet_id: string;
  player_id: string;
  payout: number;
  config_version: string;
}

// Wallet Endpoints
export interface WalletDebitRequest {
  player_id: string;
  amount: number;
  currency: Currency;
  game_code: string;
  round_id: string;
  reference: string;
}

export interface WalletCreditRequest {
  player_id: string;
  amount: number;
  currency: Currency;
  game_code: string;
  round_id: string;
  reference: string;
}

export interface WalletRollbackRequest {
  player_id: string;
  original_reference: string;
  reason: string;
}

// Game Session
export type Currency = 'COIN' | 'USD' | 'EUR' | 'USDT';

export interface SessionCreateRequest {
  player_id: string;
  game_code: string;
  currency: Currency;
  language: string;
  platform: string;
  return_url?: string;
  table_id?: string;
  amount?: number;
}

export interface SessionResponse {
  success: true;
  session_id: string;
  session_token: string;
  token_type: 'Bearer';
  game_code: string;
  table_id: string;
  currency: string;
  language: string;
  platform: string;
  return_url: string;
  launch_url: string;
  expires_at: string;
  expires_at_ms: number;
  websocket_url: string;
}

export interface SessionState {
  session_id: string;
  player_id: string;
  game_id: string;
  table_id: string;
  created_at: string;
  last_seen_at: string;
  active: boolean;
  table?: {
    round_id: string | null;
    status: string;
    betting_end_at: number;
    players: number;
  };
}

// Table & Choices
export interface TableChoice {
  choice: string;
  label: string;
  multiplier?: number;
  icon?: string;
  color_hex?: string;
  hot?: boolean;
}

export interface ChoiceListResponse {
  game_code: string;
  table_id: string;
  choice_field: string;
  choices: Array<{
    choice: string;
    label: string;
    multiplier?: number;
    icon?: string;
    color_hex?: string;
    hot?: boolean;
  }>;
}

export interface JoinResponse {
  table_id: string;
  player_id: string;
  seats: string[];
  players: number;
  max_players: number;
  status: string;
  joined: boolean;
  already_seated: boolean;
}

export interface LeaveResponse {
  table_id: string;
  player_id: string;
  seats: string[];
  removed: boolean;
}

export interface StateResponse {
  table_id: string;
  player_id: string;
  state: any;
}

export interface HistoryResponse {
  table_id: string;
  count: number;
  rounds: Array<{
    round_id: string;
    round_no: number;
    config_version: string;
    winners: string[];
    pot: number;
    settlements: Array<{
      settlement_id: string;
      bet_id: string;
      player_id: string;
      payout: number;
      config_version: string;
    }>;
    carry_out: number;
    settled_at: number;
  }>;
  bets?: Array<{
    bet_id: string;
    round_id: string;
    player_id: string;
    option_id: string;
    position?: string;
    amount: number;
    status: string;
  }>;
}

// Wallet
export type Currency = 'COIN' | 'USD' | 'EUR' | 'USDT';

export interface BalanceResponse {
  player_id: string;
  available: number;
  currency: string;
}

export interface WalletTransactionResponse {
  txn_id: string;
  type: 'debit' | 'credit' | 'rollback';
  status: 'SUCCESS' | 'ROLLED_BACK';
  player_id: string;
  amount: number;
  currency: string;
  reference: string;
  idempotency_key: string;
  game_code: string;
  round_id: string;
  original_reference?: string;
  reverses_txn_id?: string;
  reason?: string;
  actor: string;
  created_at: number;
  replayed?: boolean;
}

export interface TransactionListResponse {
  player_id: string;
  count: number;
  transactions: Array<{
    txn_id: string;
    type: 'debit' | 'credit' | 'rollback';
    status: 'SUCCESS' | 'ROLLED_BACK';
    player_id: string;
    amount: number;
    currency: string;
    reference: string;
    idempotency_key: string;
    game_code: string;
    round_id: string;
    created_at: number;
  }>;
}

// Health & Meta
export interface HealthResponse {
  status: 'ok' | 'degraded';
  game_code: string;
  engine: string;
  provider_api: string;
  provider_auth_configured: boolean;
  wallet_backend: string;
  currency: string;
  tables: number;
  live_tables: number;
  redis: boolean;
  serverTime: number;
}

// WebSocket
export interface WSMessage {
  kind: 'snapshot' | 'event' | 'error' | 'pong';
  data: any;
}

export interface WSSubscribeMessage {
  action: 'subscribe' | 'ping';
  session_token: string;
  room?: string;
}

export interface WSEvent {
  seq: number;
  kind: string;
  serverTime: number;
  data: any;
}

export interface WSSubscribeMessage {
  action: 'subscribe' | 'ping';
  session_token: string;
  room?: string;
}

export interface WSEvent {
  seq: number;
  kind: string;
  serverTime: number;
  data: any;
}

// Error Codes
export enum ErrorCode {
  UNAUTHENTICATED = 'UNAUTHENTICATED',
  FORBIDDEN = 'FORBIDDEN',
  NOT_FOUND = 'NOT_FOUND',
  VALIDATION_ERROR = 'VALIDATION_ERROR',
  INSUFFICIENT_BALANCE = 'INSUFFICIENT_BALANCE',
  DUPLICATE_REQUEST = 'DUPLICATE_REQUEST',
  STATE_CONFLICT = 'STATE_CONFLICT',
  BETTING_CLOSED = 'BETTING_CLOSED',
  RATE_LIMITED = 'RATE_LIMITED',
  INVALID_TOKEN = 'INVALID_TOKEN',
  INVALID_SESSION_TOKEN = 'INVALID_SESSION_TOKEN',
  REPLAYED_REQUEST = 'REPLAYED_REQUEST',
  TBC_RULE_UNCONFIRMED = 'TBC_RULE_UNCONFIRMED',
  INTERNAL_ERROR = 'INTERNAL_ERROR',
  UNAVAILABLE = 'UNAVAILABLE',
}

export interface ErrorResponse {
  success: false;
  code: string;
  message: string;
  data: null;
  serverTime: number;
  requestId: string;
}

export interface SuccessResponse<T> {
  success: true;
  code: 'OK';
  message: string;
  data: T;
  serverTime: number;
  requestId: string;
}

export type APIResponse<T> = { success: true; code: 'OK'; message: string; data: T; serverTime: number; requestId: string } | { success: false; code: string; message: string; data: null; serverTime: number; requestId: string };

// Pagination
export interface PaginationParams {
  limit?: number;
  offset?: number;
}

export interface PaginatedResponse<T> {
  data: T[];
  count: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

// Webhook Events
export type WebhookEventKind =
  | 'game.session.created'
  | 'player.joined'
  | 'player.left'
  | 'round.started'
  | 'card.dealt'
  | 'turn.started'
  | 'bet.required'
  | 'bet.placed'
  | 'player.folded'
  | 'show.requested'
  | 'game.finished'
  | 'round.settled'
  | 'balance.updated'
  | 'game.error';

export interface WebhookEvent {
  event_id: string;
  kind: string;
  data: any;
  serverTime: number;
}

export interface WebhookSubscription {
  id: string;
  url: string;
  events: string[];
  secret: string;
  active: boolean;
  created_at: string;
}
