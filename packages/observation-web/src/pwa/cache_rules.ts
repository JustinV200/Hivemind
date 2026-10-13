/**
 * What the service worker may cache: the app shell, never anything under `/v1/`.
 *
 * Responses from the Landing Board are live, signed and personal (ADR-0049): a cached copy could
 * show one person's data to another session, or show old state as if current. So the rule is
 * exact and tested: only same-origin GET requests outside `/v1/` are ever intercepted or stored.
 * This file is that rule and the cache's name, as pure functions, so the test needs no worker.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/pwa/`. Used by `worker.ts`; asserted by
 *   `tests/pwa/cache_rules.test.ts`.
 *
 * Key invariants:
 *   - A request under `/v1/`, of any method, is never cached and never intercepted.
 *   - Only GET is ever cached.
 *   - Raising `SHELL_CACHE`'s version retires every older shell cache at the next activation.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (PWA).
 */
export const SHELL_CACHE = 'hive-shell-v1';

// Fetched at install so the shell opens offline; paths are relative to the worker's own URL.
export const SHELL_URLS: readonly string[] = [
  './',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

export interface RequestLike {
  url: string;
  method: string;
}

export function isApiPath(pathname: string): boolean {
  return pathname === '/v1' || pathname.startsWith('/v1/');
}

export function isShellRequest(request: RequestLike, origin: string): boolean {
  if (request.method !== 'GET') return false;
  const url = new URL(request.url, origin);
  return url.origin === origin && !isApiPath(url.pathname);
}
