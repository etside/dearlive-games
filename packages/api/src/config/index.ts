/**
 * Configuration for the DearLive API Provider
 */

export interface ProviderConfig {
  // Server
  port: number;
  host: string;
  env: 'development' | 'staging' | 'production';

  // Redis
  redis: {
    host: string;
    port: number;
    username?: string;
    password?: string;
    tls: boolean;
    db: number;
  };

  // Database
  database: {
    url: string;
  };

  // Auth
  auth: {
    jwtSecret: string;
    jwtExpiry: string; // e.g., '15m', '1h', '7d'
    apiKeyPrefix: string; // e.g., 'dl_'
    sessionTtlSeconds: number;
    launchTokenTtlMs: number;
  };

  // Provider
  provider: {
    baseUrl: string;
    clientPath: string;
    providerPrefix: string;
    defaultCurrency: string;
    sessionTtlSeconds: number;
  };

  // Webhooks
  webhooks: {
    secret: string;
    deliveryTimeoutMs: number;
    maxRetries: number;
    retryDelayMs: number;
  };

  // Wallet
  wallet: {
    baseUrl: string;
    apiKey: string;
    clientId: string;
    clientSecret: string;
    currency: string;
    timeoutMs: number;
  };

  // Game Engines
  games: {
    teen_patti_pro: {
      enabled: boolean;
      confirmed: boolean;
      tableCatalog: string;
    };
    greedy_monkey: {
      enabled: boolean;
      confirmed: boolean;
      tableCatalog: string;
    };
    baby_king: {
      enabled: boolean;
      confirmed: boolean;
      tableCatalog: string;
    };
  };

  // Rate Limiting
  rateLimit: {
    default: { windowMs: number; maxRequests: number };
    auth: { windowMs: number; maxRequests: number };
    wallet: { windowMs: number; maxRequests: number };
    admin: { windowMs: number; maxRequests: number };
  };

  // CORS
  cors: {
    origins: string[];
    credentials: boolean;
  };

  // Logging
  logging: {
    level: 'debug' | 'info' | 'warn' | 'error';
    prettyPrint: boolean;
  };
}

export function loadConfig(): ProviderConfig {
  const env = process.env;

  return {
    port: parseInt(env.PORT || '3000', 10),
    host: env.HOST || '0.0.0.0',
    env: (env.NODE_ENV as any) || 'development',

    redis: {
      host: env.REDIS_HOST || 'localhost',
      port: parseInt(env.REDIS_PORT || '6379', 10),
      username: env.REDIS_USERNAME,
      password: env.REDIS_PASSWORD,
      tls: env.REDIS_TLS === 'true',
      db: parseInt(env.REDIS_DB || '0', 10),
    },

    database: {
      url: env.DATABASE_URL || '',
    },

    auth: {
      jwtSecret: env.JWT_SECRET || 'dev-secret-change-in-production',
      jwtExpiry: env.JWT_EXPIRY || '15m',
      apiKeyPrefix: env.API_KEY_PREFIX || 'dl_',
      sessionTtlSeconds: parseInt(env.SESSION_TTL_SECONDS || '1800', 10),
      launchTokenTtlMs: parseInt(env.LAUNCH_TOKEN_TTL_MS || '120000', 10),
    },

    provider: {
      baseUrl: env.PROVIDER_BASE_URL || 'http://localhost:3000',
      clientPath: env.PROVIDER_CLIENT_PATH || '/teen-patti-pro/?session=',
      providerPrefix: '/api/v1/provider',
      defaultCurrency: env.COIN_CURRENCY || 'COIN',
      sessionTtlSeconds: parseInt(env.SESSION_TTL_SECONDS || '1800', 10),
    },

    webhooks: {
      secret: env.WEBHOOK_SECRET || 'dev-webhook-secret',
      deliveryTimeoutMs: parseInt(env.WEBHOOK_DELIVERY_TIMEOUT_MS || '8000', 10),
      maxRetries: parseInt(env.WEBHOOK_MAX_RETRIES || '3', 10),
      retryDelayMs: parseInt(env.WEBHOOK_RETRY_DELAY_MS || '1000', 10),
    },

    wallet: {
      baseUrl: env.WALLET_BASE_URL || '',
      apiKey: env.WALLET_API_KEY || '',
      clientId: env.WALLET_CLIENT_ID || '',
      clientSecret: env.WALLET_CLIENT_SECRET || '',
      currency: env.COIN_CURRENCY || 'COIN',
      timeoutMs: parseInt(env.WALLET_TIMEOUT_MS || '8000', 10),
    },

    games: {
      teen_patti_pro: {
        enabled: env.TEEN_PATTI_ENABLED !== 'false',
        confirmed: env.TEEN_PATTI_CONFIRMED === 'true',
        tableCatalog: env.TEEN_PATTI_TABLE_CATALOG || 'default',
      },
      greedy_monkey: {
        enabled: env.GREEDY_MONKEY_ENABLED !== 'false',
        confirmed: env.GREEDY_MONKEY_CONFIRMED === 'true',
        tableCatalog: env.GREEDY_MONKEY_TABLE_CATALOG || 'default',
      },
      baby_king: {
        enabled: env.BABY_KING_ENABLED !== 'false',
        confirmed: env.BABY_KING_CONFIRMED === 'true',
        tableCatalog: env.BABY_KING_TABLE_CATALOG || 'default',
      },
    },

    rateLimit: {
      default: { windowMs: 60000, maxRequests: 100 },
      auth: { windowMs: 900000, maxRequests: 10 },
      wallet: { windowMs: 60000, maxRequests: 30 },
      admin: { windowMs: 60000, maxRequests: 1000 },
    },

    cors: {
      origins: (env.CORS_ORIGINS || 'http://localhost:3000,http://localhost:5173').split(','),
      credentials: true,
    },

    logging: {
      level: (env.LOG_LEVEL as any) || 'info',
      prettyPrint: env.NODE_ENV !== 'production',
    },
  };
}

export const config = loadConfig();
