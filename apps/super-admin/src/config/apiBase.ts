/**
 * API base URL resolution.
 *
 * Host-agnostic by design: this app is embedded in the DearLive app, served
 * from its own nginx, or run on localhost, and in every case the API is either
 * on the same origin or given explicitly at build time. Nothing here names a
 * domain.
 *
 * Resolution order:
 *   1. `VITE_API_BASE` — set at build time. Use for a cross-origin API, e.g.
 *      `VITE_API_BASE=https://api.example.com npm run build`.
 *   2. Same origin — the default. Works when a reverse proxy serves the API
 *      and the client from one host, and when the app is embedded.
 *
 * A trailing slash is stripped so callers can always append `/api/v1/...`
 * without producing a double slash.
 */
const RAW_BASE =
  (typeof import.meta !== 'undefined' &&
    (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_BASE) ||
  '';

export const API_BASE = RAW_BASE.replace(/\/+$/, '');

/** True when the API lives on a different origin than this page. */
export const API_IS_CROSS_ORIGIN = API_BASE !== '' && !API_BASE.startsWith('/');

/**
 * Resolve an endpoint to a full URL.
 *
 * Absolute URLs are passed through untouched, so a caller can still point one
 * request at a different host.
 */
export function apiUrl(endpoint: string): string {
  if (/^https?:\/\//i.test(endpoint)) return endpoint;
  return `${API_BASE}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;
}

/**
 * Warning for the developer who built this without thinking about the API host.
 * Cross-origin without a configured base is the one case that cannot work, so
 * it is worth saying out loud rather than failing on the first request.
 */
export function describeApiBase(): string {
  if (API_IS_CROSS_ORIGIN) {
    return `API base: ${API_BASE} (from VITE_API_BASE)`;
  }
  return (
    'API base: same origin. Set VITE_API_BASE at build time if the API is ' +
    'hosted elsewhere.'
  );
}
