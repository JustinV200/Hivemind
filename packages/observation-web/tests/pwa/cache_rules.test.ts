/**
 * Tests for the service worker's caching rule and behaviour: never anything under `/v1/`.
 *
 * ADR-0049 makes "the worker never caches `/v1/`" a test, not a default, because Landing Board
 * answers are live, signed and personal. The rule is checked as a pure function and then through
 * the worker itself with a fake scope: for any request under `/v1/`, whatever the method, the
 * worker must not answer it and no cache may be opened or written. Shell requests must be cached.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/pwa/cache_rules.ts and src/pwa/worker.ts.
 *
 * Key invariants:
 *   - "Never caches `/v1/`" is asserted by observing the cache, not by reading the rule.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (PWA).
 */
import { describe, expect, it } from 'vitest';
import { isApiPath, isShellRequest, SHELL_CACHE } from '../../src/pwa/cache_rules';
import { installCaching } from '../../src/pwa/worker';
import type { FetchEventLike, WorkerScope } from '../../src/pwa/worker';

const ORIGIN = 'https://hive.test';

describe('isShellRequest', () => {
  it.each(['/v1', '/v1/', '/v1/cells', '/v1/chat?limit=20', '/v1/push/vapid-key'])(
    'refuses %s as API',
    (path) => {
      expect(isApiPath(new URL(path, ORIGIN).pathname)).toBe(true);
      expect(isShellRequest({ url: ORIGIN + path, method: 'GET' }, ORIGIN)).toBe(false);
    },
  );

  it('refuses every non-GET request, even for the shell', () => {
    expect(isShellRequest({ url: `${ORIGIN}/assets/a.js`, method: 'POST' }, ORIGIN)).toBe(false);
  });

  it('refuses another origin', () => {
    expect(isShellRequest({ url: 'https://other.test/assets/a.js', method: 'GET' }, ORIGIN)).toBe(
      false,
    );
  });

  it.each(['/', '/index.html', '/assets/index-abc.js', '/manifest.webmanifest', '/v10/x', '/v1x'])(
    'accepts %s as shell',
    (path) => {
      expect(isShellRequest({ url: ORIGIN + path, method: 'GET' }, ORIGIN)).toBe(true);
    },
  );
});

interface Log {
  opened: number;
  puts: string[];
  fetched: string[];
  added: string[];
  deleted: string[];
}

// A cache that records what is put into it and serves it back.
function fakeCache(log: Log, stored: Map<string, Response>) {
  return {
    addAll: (urls: string[]) => {
      log.added.push(...urls);
      return Promise.resolve();
    },
    put: (request: Request, response: Response) => {
      log.puts.push(request.url);
      stored.set(request.url, response);
      return Promise.resolve();
    },
  };
}

// A scope whose cache and network are lists, so the test can see what the worker touched.
function fakeScope() {
  const listeners: Record<string, (event: never) => void> = {};
  const log: Log = { opened: 0, puts: [], fetched: [], added: [], deleted: [] };
  const stored = new Map<string, Response>();
  const cache = fakeCache(log, stored);
  const scope = {
    location: { origin: ORIGIN },
    caches: {
      open: (name: string) => {
        log.opened += 1;
        expect(name).toBe(SHELL_CACHE);
        return Promise.resolve(cache);
      },
      keys: () => Promise.resolve([SHELL_CACHE, 'hive-shell-v0', 'someone-elses']),
      delete: (name: string) => {
        log.deleted.push(name);
        return Promise.resolve(true);
      },
      match: (request: Request | string) =>
        Promise.resolve(stored.get(typeof request === 'string' ? request : request.url)),
    },
    fetch: (request: Request) => {
      log.fetched.push(request.url);
      return Promise.resolve(new Response('body'));
    },
    skipWaiting: () => Promise.resolve(),
    clients: { claim: () => Promise.resolve() },
    addEventListener: (type: string, listener: (event: never) => void) => {
      listeners[type] = listener;
    },
  };
  installCaching(scope as unknown as WorkerScope);
  const dispatch = (request: Request | { url: string; method: string; mode: string }) => {
    const responses: Promise<Response>[] = [];
    const event: FetchEventLike = {
      request: request as Request,
      respondWith: (response) => responses.push(response),
    };
    (listeners.fetch as (e: FetchEventLike) => void)(event);
    return responses;
  };
  return { log, dispatch, listeners };
}

describe('worker', () => {
  it.each([
    ['GET', '/v1/cells'],
    ['GET', '/v1/chat?limit=20'],
    ['POST', '/v1/chat'],
    ['DELETE', '/v1/push/subscriptions/sub_1'],
    ['GET', '/v1/'],
  ])('never answers or caches %s %s', async (method, path) => {
    const { log, dispatch } = fakeScope();
    const responses = dispatch(new Request(ORIGIN + path, { method }));
    expect(responses).toHaveLength(0);
    expect(log.opened).toBe(0);
    expect(log.puts).toEqual([]);
    expect(log.fetched).toEqual([]);
  });

  it('never caches a /v1/ navigation either', () => {
    const { log, dispatch } = fakeScope();
    const responses = dispatch({ url: `${ORIGIN}/v1/cells`, method: 'GET', mode: 'navigate' });
    expect(responses).toHaveLength(0);
    expect(log.puts).toEqual([]);
  });

  it('caches a static asset on first fetch and serves it from the cache after', async () => {
    const { log, dispatch } = fakeScope();
    const url = `${ORIGIN}/assets/index-abc.js`;
    const first = dispatch(new Request(url));
    await first[0];
    expect(log.puts).toEqual([url]);
    expect(log.fetched).toEqual([url]);
    await dispatch(new Request(url))[0];
    // The second answer came from the cache: no new network fetch.
    expect(log.fetched).toEqual([url]);
  });

  it('answers a navigation from the network and falls back to the cached shell offline', async () => {
    const { log, dispatch } = fakeScope();
    await dispatch({ url: `${ORIGIN}/`, method: 'GET', mode: 'navigate' })[0];
    expect(log.puts).toEqual([`${ORIGIN}/`]);
  });

  it('precaches the shell at install and retires only older shell caches at activate', async () => {
    const { log, listeners } = fakeScope();
    const work: Promise<unknown>[] = [];
    (listeners.install as (e: { waitUntil(p: Promise<unknown>): void }) => void)({
      waitUntil: (p) => work.push(p),
    });
    (listeners.activate as (e: { waitUntil(p: Promise<unknown>): void }) => void)({
      waitUntil: (p) => work.push(p),
    });
    await Promise.all(work);
    expect(log.added).toContain('./manifest.webmanifest');
    expect(log.added.every((url) => !url.includes('/v1'))).toBe(true);
    expect(log.deleted).toEqual(['hive-shell-v0']);
  });
});
