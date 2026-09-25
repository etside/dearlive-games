/**
 * Wallet Adapter Interface
 * Abstracts the wallet implementation (Redis, HTTP, etc.)
 */
export interface Balance {
  player_id: string;
  available: number;
  currency: string;
}

export interface TxnRef {
  txn_id: string;
  idempotency_key: string;
}

export class WalletError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WalletError';
  }
}

export class InsufficientBalance extends WalletError {
  constructor(playerId: string) {
    super(`Insufficient balance for player ${playerId}`);
    this.name = 'InsufficientBalance';
  }
}

export interface WalletAdapter {
  getBalance(playerId: string): Promise<{ player_id: string; available: number; currency: string }>;
  debit(playerId: string, amount: number, ref: string, idempotencyKey: string): Promise<{ txn_id: string; idempotency_key: string }>;
  credit(playerId: string, amount: number, ref: string, idempotencyKey: string): Promise<{ txn_id: string; idempotency_key: string }>;
  voidDebit?(playerId: string, ref: string, idempotencyKey: string): Promise<{ txn_id: string; idempotency_key: string }>;
}
