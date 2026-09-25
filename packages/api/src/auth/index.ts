/**
 * Authentication module for the DearLive API Provider
 * Handles API key authentication, JWT tokens, and HMAC signatures
 */
import { createHmac, timingSafeEqual } from 'crypto';
import { JWTPayload, APIKey, ErrorCode } from '../types';

export class AuthError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number = 401
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

export class AuthService {
  private apiKeys: Map<string, APIKey> = new Map();
  private jwtSecret: string;
  private jwtExpiry: string;
  private apiKeyPrefix: string;
  private nonceStore: Map<string, number> = new Map(); // nonce -> timestamp
  private readonly NONCE_TTL_MS = 5 * 60 * 1000; // 5 minutes

  constructor(jwtSecret: string, jwtExpiry: string, apiKeyPrefix: string) {
    this.jwtSecret = jwtSecret;
    this.jwtExpiry = jwtExpiry;
    this.apiKeyPrefix = apiKeyPrefix;
  }

  /**
   * Register an API key
   */
  registerApiKey(key: APIKey): void {
    if (!key.key_id.startsWith(this.apiKeyPrefix)) {
      throw new Error(`API key must start with prefix: ${this.apiKeyPrefix}`);
    }
    this.apiKeys.set(key.key_id, key);
  }

  /**
   * Validate API key and HMAC signature
   */
  async authenticateRequest(
    method: string,
    path: string,
    headers: Record<string, string>,
    body: string
  ): Promise<{ key: APIKey; payload: any }> {
    const apiKeyId = this.getHeader(headers, 'x-api-key');
    const timestamp = this.getHeader(headers, 'x-timestamp');
    const nonce = this.getHeader(headers, 'x-nonce');
    const signature = this.getHeader(headers, 'x-signature');

    if (!apiKeyId || !timestamp || !nonce || !signature) {
      throw new AuthError(ErrorCode.UNAUTHENTICATED, 'Missing authentication headers');
    }

    // Verify timestamp (5 min window)
    const ts = parseInt(timestamp, 10);
    if (isNaN(ts) || Math.abs(Date.now() - ts) > 5 * 60 * 1000) {
      throw new AuthError(ErrorCode.INVALID_TIMESTAMP, 'Timestamp outside acceptable window');
    }

    // Check nonce replay
    const nonceKey = `${apiKeyId}:${nonce}`;
    const nonceTime = this.nonceStore.get(nonceKey);
    if (nonceTime && Date.now() - nonceTime < this.NONCE_TTL_MS) {
      throw new AuthError(ErrorCode.REPLAYED_REQUEST, 'Nonce already used');
    }
    this.nonceStore.set(nonceKey, Date.now());

    // Verify API key exists
    const apiKey = this.apiKeys.get(apiKeyId);
    if (!apiKey || apiKey.revoked) {
      throw new AuthError(ErrorCode.UNAUTHENTICATED, 'Invalid or revoked API key');
    }

    // Verify signature
    const canonicalString = this.createCanonicalString(method, path, timestamp, nonce, body);
    const expectedSignature = this.createSignature(apiKey.key_secret, canonicalString);
    
    if (!timingSafeEqual(Buffer.from(signature), Buffer.from(expectedSignature))) {
      throw new AuthError(ErrorCode.INVALID_SIGNATURE, 'Invalid signature');
    }

    // Store nonce
    this.nonceStore.set(nonceKey, Date.now());

    return { key: apiKey, payload: {} };
  }

  /**
   * Create JWT token for player sessions
   */
  createSessionToken(payload: Omit<JWTPayload, 'iat' | 'exp'>): string {
    const now = Math.floor(Date.now() / 1000);
    const payload: any = {
      ...payload,
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + this.parseExpiry(this.jwtExpiry),
    };
    return this.signJWT(payload);
  }

  /**
   * Verify JWT token
   */
  verifySessionToken(token: string): any {
    // Implementation would use jose library
    // For now, return mock
    return { valid: true };
  }

  /**
   * Create launch token (short-lived, for player game launch)
   */
  createLaunchToken(payload: {
    player_id: string;
    room_id: string;
    game_id: string;
  }): string {
    const token = `gst_${this.generateSecureToken(32)}`;
    // Store in Redis with TTL
    return token;
  }

  /**
   * Verify launch token
   */
  async verifyLaunchToken(token: string): Promise<{ valid: boolean; payload?: any }> {
    // Implementation would check Redis
    return { valid: true, payload: {} };
  }

  private getHeader(headers: Record<string, string>, name: string): string | undefined {
    const key = name.toLowerCase();
    for (const [key, value] of Object.entries(headers)) {
      if (key.toLowerCase() === key) return headers[key];
    }
    return undefined;
  }

  private createCanonicalString(method: string, path: string, timestamp: string, nonce: string, body: string): string {
    const bodyHash = this.hashBody(body);
    return `${method.toUpperCase()}\n${path}\n${timestamp}\n${nonce}\n${bodyHash}`;
  }

  private createSignature(secret: string, canonicalString: string): string {
    return createHmac('sha256', secret).update(canonicalString).digest('hex');
  }

  private hashBody(body: string): string {
    return createHash('sha256').update(body || '').digest('hex');
  }

  private parseExpiry(expiry: string): number {
    // Parse strings like '15m', '1h', '7d' into seconds
    const match = expiry.match(/^(\d+)([mhd])$/);
    if (!match) return 900; // default 15 minutes
    const value = parseInt(match[1], 10);
    const unit = match[2];
    switch (unit) {
      case 'm': return value * 60;
      case 'h': return value * 60 * 60;
      case 'd': return value * 60 * 60 * 24;
      default: return 900;
    }
  }

  private generateSecureToken(length: number): string {
    const bytes = new Uint8Array(length);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }

  // Clean up expired nonces periodically
  cleanupNonces(): void {
    const now = Date.now();
    for (const [key, time] of this.nonceStore.entries()) {
      if (now - time > this.NONCE_TTL_MS) {
        this.nonceStore.delete(key);
      }
    }
  }
}

export class AuthError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number = 401
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

// Helper functions
function createHash(algorithm: string): any {
  return {
    update: (data: string) => ({
      digest: (encoding: string) => {
        // In production, use crypto.createHash
        return 'mock-hash';
      }
    })
  };
}

function createHmac(algorithm: string, key: string): any {
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
