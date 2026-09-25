/**
 * Wallet Service for the DearLive API Provider
 * Handles player balances, debits, credits, and transaction history
 */
import { WalletAdapter, Balance, TxnRef, WalletError, InsufficientBalance } from '../types';
import { WalletAdapter as WalletAdapterType } from '../types';

export class WalletService {
  private adapter: WalletAdapter;

  constructor(adapter: WalletAdapter) {
    this.adapter = adapter;
  }

  /**
   * Get player balance
   */
  async getBalance(playerId: string): Promise<{ player_id: string; available: number; currency: string }> {
    const balance = await this.adapter.getBalance(playerId);
    return {
      player_id: balance.player_id,
      available: balance.available,
      currency: balance.currency,
    };
  }

  /**
   * Debit player balance (reserve stake)
   */
  async debit(
    playerId: string,
    amount: number,
    reference: string,
    idempotencyKey: string
  ): Promise<{ txn_id: string; idempotency_key: string }> {
    if (amount <= 0) {
      throw new Error('Amount must be positive');
    }
    return this.adapter.debit(playerId, amount, reference, idempotencyKey);
  }

  /**
   * Credit player balance (payout)
   */
  async credit(
    playerId: string,
    amount: number,
    reference: string,
    idempotencyKey: string
  ): Promise<{ txn_id: string; idempotency_key: string }> {
    if (amount < 0) {
      throw new Error('Amount must be non-negative');
    }
    return this.adapter.credit(playerId, amount, reference, idempotencyKey);
  }

  /**
   * Rollback a previous debit (refund)
   */
  async rollback(
    playerId: string,
    originalReference: string,
    reason: string,
    idempotencyKey: string
  ): Promise<{ txn_id: string; idempotency_key: string }> {
    // In production, this would call a rollback/refund endpoint
    // For now, we do a compensating credit
    return this.adapter.credit(
      playerId,
      0, // Amount would be looked up from original transaction
      `rollback:${originalReference}`,
      idempotencyKey
    );
  }

  /**
   * Get transaction history for a player
   */
  async getTransactions(playerId: string, limit = 50): Promise<Array<{
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
  }>> {
    // In production, this would query the transaction log
    // For now, return empty array as the adapter handles the ledger
    return [];
  }
}

// Helper functions that would be implemented with actual crypto
function createHash(algorithm: string) {
  return {
    update: (data: string) => ({
      digest: (encoding: string) => {
        // In production, use crypto.createHash
        return 'mock-hash';
      }
    })
  }

  function createHmac(algorithm: string, key: string) {
    return {
      update: (data: string) => ({
        digest: (encoding: string) => {
          // In production, use crypto.createHmac
          return 'mock-signature';
        }
      })
    };
  }

  function timingSafeEqual(a: Buffer, b: Buffer): boolean {
    return a.length === b.length && a.every((val, i) => val === b[i]);
  }
}
