/**
 * Main API Router - handles all provider API routes
 */
import { ProviderContext, buildContext } from './context';
import { authenticateRequest, AuthError } from '../auth';
import { ProviderWallet } from '../wallet/provider_wallet';
import { canonical_code, BINDINGS, binding_for_slug, binding_for_code, TEEN_CODE, LION_CODE, MONKEY_CODE } from './games';
import { TableCatalog, list_tables, table_detail, room_status, select_table, seat_count } from './tables';
import { ProviderError, ProviderErrorCode } from './errors';
import { ProviderContext, buildContext } from './context';
import { Request, Response } from './types';

const PROVIDER_PREFIX = '/api/v1/provider';
const GAME_SLUGS = ['teen-patti', 'greedy-lion', 'monkey-wheel'];
const GAME_SLUG_PATTERN = '(teen-patti|greedy-lion|monkey-wheel)';

export async function handleRequest(
  method: string,
  path: string,
  query: Record<string, string>,
  headers: Record<string, string>,
  body: string
): Promise<{ status: number; headers: Record<string, string>; body: string }> {
  // Build context (in production, this would be a real context)
  const ctx = buildContext({});
  
  try {
    // Check if this is a provider route
    if (!isProviderPath(path)) {
      return { status: 404, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ error: 'Not found' }) };
    }

    // Parse body
    let body: any = null;
    if (body) {
      try {
        body = JSON.parse(body);
      } catch {
        return errorResponse(400, 'INVALID_JSON', 'Invalid JSON body');
      }
    }

    // Route matching
    const route = matchRoute(method, path);
    if (!route) {
      return errorResponse(404, 'NOT_FOUND', 'Route not found');
    }

    // Authentication
    let actor = '';
    if (route.auth !== 'public') {
      try {
        const auth = await authenticateRequest(method, path, {}, '');
        // In real implementation, this would validate the request
      } catch (error) {
        return errorResponse(401, 'UNAUTHENTICATED', 'Authentication required');
      }
    }

    // Route to handler
    try {
      const result = await route.handler({} as any, {} as any);
      return successResponse(result);
    } catch (error) {
      return handleError(error);
    }
  } catch (error) {
    return handleError(error);
  }
}

function isProviderPath(path: string): boolean {
  if (path.startsWith('/api/v1/')) return true;
  if (path === '/docs' || path === '/docs/') return true;
  if (path === '/openapi.json') return true;
  if (path.startsWith('/api/v1/provider/')) return true;
  if (path === '/api/v1/games') return true;
  if (path.match(/^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\//)) return true;
  if (path === '/api/v1/sessions' || path.match(/^\/api\/v1\/sessions\/[^/]+$/)) return true;
  if (path.startsWith('/api/v1/players/')) return true;
  if (path.startsWith('/api/v1/wallet/')) return true;
  if (path.startsWith('/api/v1/provider/')) return true;
  if (path === '/docs' || path === '/openapi.json') return true;
  return false;
}

interface Route {
  method: string;
  pattern: RegExp;
  handler: (ctx: any, req: any) => Promise<any>;
  auth: 'public' | 'hmac' | 'hmac_or_token';
}

function matchRoute(method: string, path: string): Route | null {
  const routes: Route[] = [
    { method: 'GET', pattern: /^\/api\/v1\/provider\/health$/, handler: h_health, auth: 'public' },
    { method: 'GET', pattern: /^\/api\/v1\/games$/, handler: h_games, auth: 'hmac' },
    { method: 'POST', pattern: /^\/api\/v1\/sessions$/, handler: h_create_session, auth: 'hmac_or_token' },
    { method: 'GET', pattern: /^\/api\/v1\/sessions\/([^/]+)$/, handler: h_get_session, auth: 'hmac_or_token' },
    { method: 'DELETE', pattern: /^\/api\/v1\/sessions\/([^/]+)$/, handler: h_delete_session, auth: 'hmac' },
    { method: 'GET', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables$/, handler: h_list_tables, auth: 'hmac' },
    { method: 'GET', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables\/([^/]+)$/, handler: h_table_detail, auth: 'hmac' },
    { method: 'GET', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables\/([^/]+)\/choices$/, handler: h_choices, auth: 'hmac' },
    { method: 'POST', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables\/([^/]+)\/join$/, handler: h_join, auth: 'hmac' },
    { method: 'POST', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables\/([^/]+)\/leave$/, handler: h_leave, auth: 'hmac' },
    { method: 'POST', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables\/([^/]+)\/action$/, handler: h_action, auth: 'hmac' },
    { method: 'GET', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables\/([^/]+)\/state$/, handler: h_state, auth: 'hmac' },
    { method: 'GET', pattern: /^\/api\/v1\/(teen-patti|greedy-lion|monkey-wheel)\/tables\/([^/]+)\/history$/, handler: h_history, auth: 'hmac' },
    { method: 'GET', pattern: /^\/api\/v1\/players\/([^/]+)\/balance$/, handler: h_balance, auth: 'hmac' },
    { method: 'POST', pattern: /^\/api\/v1\/wallet\/debit$/, handler: h_debit, auth: 'hmac' },
    { method: 'POST', pattern: /^\/api\/v1\/wallet\/credit$/, handler: h_credit, auth: 'hmac' },
    { method: 'POST', pattern: /^\/api\/v1\/wallet\/rollback$/, handler: h_rollback, auth: 'hmac' },
    { method: 'GET', pattern: /^\/api\/v1\/wallet\/transactions\/([^/]+)$/, handler: h_transactions, auth: 'hmac' },
    { method: 'GET', pattern: /^\/openapi\.json$/, handler: h_openapi, auth: 'public' },
    { method: 'GET', pattern: /^\/docs\/?$/, handler: h_docs, auth: 'public' },
    { method: 'GET', pattern: /^\/api\/v1\/provider\/openapi\.json$/, handler: h_openapi, auth: 'public' },
    { method: 'GET', pattern: /^\/api\/v1\/provider\/launch\/([^/]+)$/, handler: h_launch, auth: 'public' },
  ];

  for (const route of routes) {
    if (route.method !== method) continue;
    const match = path.match(route.pattern);
    if (match) {
      return { ...route, params: match.slice(1) };
    }
  }
  return null;
}

// Placeholder handlers
async function h_health(ctx: any, req: any) {
  return { status: 'ok', game_code: 'teen_patti_pro', engine: 'TeenPattiPro/1.0', provider_api: 'v1', provider_auth_configured: true, wallet_backend: 'MemoryWallet', currency: 'COIN', tables: 0, live_tables: 0, redis: false, serverTime: Date.now() };
}

async function h_games(ctx: any, req: any) {
  return { games: [] };
}

async function h_create_session(ctx: any, req: any) {
  return { success: true, session_id: 'sess_1', session_token: 'gst_test', launch_url: '/teen-patti-pro/?session=test', expires_at: new Date(Date.now() + 1800000).toISOString() };
}

async function h_get_session(ctx: any, req: any) {
  return { session_id: 'sess_1', player_id: 'test', game_id: 'teen-patti-pro', table_id: 'default', created_at: new Date().toISOString(), last_seen_at: new Date().toISOString(), active: true };
}

async function h_delete_session(ctx: any, req: any) {
  return { session_id: 'sess_1', ended: true, revoked_tokens: 1 };
}

async function h_list_tables(ctx: any, req: any) {
  return { tables: [] };
}

async function h_table_detail(ctx: any, req: any) {
  return { table_id: 'test', label: 'Test', min_bet: 10, max_bet: 1000, max_players: 6, currency: 'COIN', enabled: true, live: { round_id: '', status: 'WAITING', betting_end_at: 0, players: 0 } };
}

async function h_choices(ctx: any, req: any) {
  return { choice_field: 'position', choices: [{ choice: 'A', label: 'Player A' }] };
}

async function h_join(ctx: any, req: any) {
  return { table_id: 'test', player_id: 'p1', seats: ['p1'], players: 1, max_players: 6, status: 'BETTING_OPEN', joined: true };
}

async function h_leave(ctx: any, req: any) {
  return { table_id: 'test', player_id: 'p1', seats: [], removed: true };
}

async function h_action(ctx: any, req: any) {
  return { table_id: 'test', player_id: 'p1', action: 'bet', accepted: true, bet_id: 'bet_1', round_id: 'round_1', position: 'A', amount: 100 };
}

async function h_state(ctx: any, req: any) {
  return { table_id: 'test', player_id: 'p1', state: { room_id: 'test', round_id: 'r1', status: 'BETTING_OPEN', betting_end_at: Date.now() + 20000, pots: {}, pot_total: 0, my_bet: 0, carry_in: 0, hands: {}, winners: [], config_version: '1.0' } };
}

async function h_history(ctx: any, req: any) {
  return { table_id: 'test', count: 0, rounds: [] };
}

async function h_balance(ctx: any, req: any) {
  return { player_id: 'p1', available: 10000, currency: 'COIN' };
}

async function h_debit(ctx: any, req: any) {
  return { txn_id: 'txn_1', idempotency_key: 'test' };
}

async function h_credit(ctx: any, req: any) {
  return { txn_id: 'txn_1', idempotency_key: 'test' };
}

async function h_rollback(ctx: any, req: any) {
  return { txn_id: 'txn_1', idempotency_key: 'test' };
}

async function h_transactions(ctx: any, req: any) {
  return { player_id: 'p1', count: 0, transactions: [] };
}

async function h_openapi(ctx: any, req: any) {
  return { openapi: '3.1.0', info: { title: 'DearLive Games API', version: '1.0.0' }, paths: {} };
}

async function h_docs(ctx: any, req: any) {
  return '<html><body>API Documentation</body></html>';
}

async function h_launch(ctx: any, req: any) {
  return { __redirect__: '/teen-patti-pro/?session=test' };
}

function successResponse(data: any) {
  return {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ success: true, code: 'OK', message: 'OK', data, serverTime: Date.now(), requestId: 'req_' + Date.now() })
  };
}

function errorResponse(status: number, code: string, message: string) {
  return {
    status,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ success: false, code, message, data: null, serverTime: Date.now(), requestId: 'req_' + Date.now() })
  };
}

function successResponse(data: any) {
  return {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ success: true, code: 'OK', message: 'OK', data, serverTime: Date.now(), requestId: 'req_' + Date.now() })
  };
}

function errorResponse(status: number, code: string, message: string) {
  return {
    status,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ success: false, code, message, data: null, serverTime: Date.now(), requestId: 'req_' + Date.now() })
  };
}

function handleError(error: any) {
  if (error instanceof Error) {
    return errorResponse(500, 'INTERNAL_ERROR', error.message);
  }
  return errorResponse(500, 'INTERNAL_ERROR', 'Unknown error');
}
