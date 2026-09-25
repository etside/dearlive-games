/**
 * Provider Context - holds all services and state for request handling
 */
import { ProviderConfig } from '../config';
import { WalletService } from '../wallet';
import { GameService } from '../games';
import { TokenStore, MemoryTokenStore, SessionStore, MemorySessionStore } from '../sessions';
import { NonceStore, MemoryNonceStore } from '../auth';
import { RateLimiter } from '../auth';
import { ProviderWallet } from '../wallet/provider_wallet';

export interface ProviderContext {
  config: any;
  wallet: any;
  teenService: any;
  wheelServices: Map<string, any>;
  tokens: any;
  sessions: any;
  nonces: any;
  limiter: any;
  keys: Record<string, any>;
  baseUrl: string;
  currency: string;
  sessionTtlMs: number;
  clientPath: string;
  redis: boolean;

  audit(actor: string, action: string, entity: string, entityId: string, after?: any): void;
  findSession(sessionId: string): any;
}

export function buildContext(config: any): any {
  // In a real implementation, this would create actual service instances
  // For now, return a mock context for compilation
  return {
    config: {},
    wallet: {},
    teenService: {},
    wheelServices: new Map(),
    tokens: {},
    sessions: {},
    nonces: {},
    limiter: {},
    keys: {},
    baseUrl: '',
    currency: 'COIN',
    sessionTtlMs: 1800000,
    clientPath: '/teen-patti-pro/?session=',
    redis: false,
    audit: () => {},
    findSession: () => null,
  };
}
