/**
 * `useRoute()`: the current route, re-read whenever the hash changes.
 *
 * The hash is the one piece of navigation state, and it lives in the browser, so the hook
 * subscribes to `hashchange` through `useSyncExternalStore` rather than copying it into React
 * state. The session's listener decides whether a loopback-only screen resolves at all.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/router/`. Used by the shell's nav (the current item) and the
 *   outlet (which view to draw).
 *
 * Key invariants:
 *   - The snapshot is the hash string, which is stable between changes; the route is derived
 *     from it with `useMemo`, so the same hash and listener return the same object.
 *   - Needs a `HiveProvider` above it, for the listener.
 *
 * See Also:
 *   - src/router/routes.ts for how a hash becomes a route.
 */
import { useMemo, useSyncExternalStore } from 'react';
import { useSlice } from '../store';
import { resolveRoute } from './routes';
import type { Route } from './routes';

function subscribe(listener: () => void): () => void {
  window.addEventListener('hashchange', listener);
  return () => window.removeEventListener('hashchange', listener);
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  const { listener } = useSlice('session');
  return useMemo(() => resolveRoute(hash, listener), [hash, listener]);
}
