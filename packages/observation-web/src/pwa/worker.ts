/**
 * The service worker's behaviour: precache the shell, serve it, and stay out of `/v1/`.
 *
 * Install stores the shell files; activate drops older shell caches; fetch answers shell
 * requests only (see `cache_rules.ts`): a page navigation tries the network first and falls back
 * to the cached shell when offline, and a static asset is served from the cache first and filled
 * from the network on a miss. Any other request, and every request under `/v1/`, is left alone
 * so the browser sends it straight to the network.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/pwa/`. Wired to the real worker scope by `service_worker.ts`;
 *   tests drive it with a fake scope.
 *
 * Key invariants:
 *   - For a request that is not a shell request, `respondWith` is never called and nothing is
 *     written to a cache.
 *   - Only successful same-origin responses are stored.
 *
 * See Also:
 *   - src/pwa/cache_rules.ts for the rule and tests/pwa/worker.test.ts for the proof.
 */
import { isShellRequest, SHELL_CACHE, SHELL_URLS } from './cache_rules';

export interface ExtendableEventLike {
  waitUntil(work: Promise<unknown>): void;
}

export interface FetchEventLike {
  request: Request;
  respondWith(response: Promise<Response>): void;
}

// The part of ServiceWorkerGlobalScope this file uses, so tests can supply a small fake.
export interface WorkerScope {
  location: { origin: string };
  caches: CacheStorage;
  fetch(request: Request): Promise<Response>;
  skipWaiting(): Promise<void>;
  clients: { claim(): Promise<void> };
  addEventListener(type: 'install' | 'activate', listener: (e: ExtendableEventLike) => void): void;
  addEventListener(type: 'fetch', listener: (e: FetchEventLike) => void): void;
}

async function precache(scope: WorkerScope): Promise<void> {
  const cache = await scope.caches.open(SHELL_CACHE);
  await cache.addAll([...SHELL_URLS]);
  await scope.skipWaiting();
}

async function retireOldCaches(scope: WorkerScope): Promise<void> {
  for (const name of await scope.caches.keys()) {
    if (name.startsWith('hive-shell-') && name !== SHELL_CACHE) await scope.caches.delete(name);
  }
  await scope.clients.claim();
}

async function remember(scope: WorkerScope, request: Request, response: Response): Promise<void> {
  // Opaque or failed answers are not worth keeping and could poison the shell.
  if (response.ok && response.type !== 'opaque') {
    const cache = await scope.caches.open(SHELL_CACHE);
    await cache.put(request, response.clone());
  }
}

async function answer(scope: WorkerScope, request: Request): Promise<Response> {
  if (request.mode === 'navigate') {
    // Network first so a new build shows at once; the cached shell is the offline fallback.
    try {
      const fresh = await scope.fetch(request);
      await remember(scope, request, fresh);
      return fresh;
    } catch {
      const cached = await scope.caches.match('./');
      if (cached) return cached;
      throw new Error('offline and no cached shell');
    }
  }
  const cached = await scope.caches.match(request);
  if (cached) return cached;
  const fresh = await scope.fetch(request);
  await remember(scope, request, fresh);
  return fresh;
}

export function installCaching(scope: WorkerScope): void {
  scope.addEventListener('install', (event) => event.waitUntil(precache(scope)));
  scope.addEventListener('activate', (event) => event.waitUntil(retireOldCaches(scope)));
  scope.addEventListener('fetch', (event) => {
    // Leaving the event untouched sends the request to the network as if no worker existed.
    if (!isShellRequest(event.request, scope.location.origin)) return;
    event.respondWith(answer(scope, event.request));
  });
}
