/**
 * The route table: every hash route the Observation Hive answers, and how a hash resolves.
 *
 * Routes are hash-based (ADR-0046) because the Entrance serves static files with no fallback and
 * the Capacitor APK loads from `file://`; only the hash survives a reload or a pasted link. This
 * file is pure: it turns a hash string and the session's listener into a `Route`, and a route
 * back into a hash. The loopback-only screens resolve to `not_found` off the Hive Stand, which is
 * defence in depth only, as the remote listener answers those routes with a 404 itself.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/router/`. Used by `use_route.ts`, the shell's nav and outlet.
 *
 * Key invariants:
 *   - Every route name in the table resolves; anything else is `not_found`.
 *   - Parameters are percent-decoded once; a malformed escape makes the route `not_found`.
 *   - `#/thoughts`, `#/attendant` and `#/honey` without a parameter mean the Queen, the Queen and
 *     the root, so the nav can link to the bare names.
 *
 * See Also:
 *   - docs/observation/design.md section 5 for the route list.
 *   - docs/adr/0046-observation-hive-dashboard-stack.md for hash routing and loopback screens.
 */
import type { Listener } from '../store/session';

export type Route =
  | { name: 'fleet' }
  | { name: 'cell'; id: string }
  | { name: 'forage' }
  | { name: 'thoughts'; bee: string }
  | { name: 'attendant'; principal: string }
  | { name: 'chat' }
  | { name: 'honey'; path: string }
  | { name: 'cost' }
  | { name: 'capping' }
  | { name: 'devices' }
  | { name: 'enrol' }
  | { name: 'not_found' };

export type RouteName = Route['name'];

// Screens that exist only on the Hive Stand (invite, pending, approve, deny, unlock, revoke, reopen).
export const LOOPBACK_ONLY: ReadonlySet<RouteName> = new Set(['enrol']);

export const NOT_FOUND: Route = { name: 'not_found' };

function segmentsOf(hash: string): string[] | null {
  const trimmed = hash.replace(/^#/, '').replace(/^\/+/, '').replace(/\/+$/, '');
  try {
    return trimmed === '' ? [] : trimmed.split('/').map(decodeURIComponent);
  } catch {
    return null;
  }
}

function parse(segments: string[]): Route {
  const [head, first, ...rest] = segments;
  // A route with no parameter takes no extra segments.
  const bare = (route: Route): Route => (first === undefined ? route : NOT_FOUND);
  switch (head) {
    case undefined:
    case 'fleet':
      return segments.length <= 1 ? { name: 'fleet' } : NOT_FOUND;
    case 'cells':
      return first && rest.length === 0 ? { name: 'cell', id: first } : NOT_FOUND;
    case 'forage':
      return bare({ name: 'forage' });
    case 'thoughts':
      return rest.length === 0 ? { name: 'thoughts', bee: first ?? 'queen' } : NOT_FOUND;
    case 'attendant':
      return rest.length === 0 ? { name: 'attendant', principal: first ?? 'queen' } : NOT_FOUND;
    case 'chat':
      return bare({ name: 'chat' });
    case 'honey':
      return { name: 'honey', path: segments.slice(1).join('/') };
    case 'cost':
      return bare({ name: 'cost' });
    case 'capping':
      return bare({ name: 'capping' });
    case 'devices':
      return bare({ name: 'devices' });
    case 'enrol':
      return bare({ name: 'enrol' });
    default:
      return NOT_FOUND;
  }
}

export function resolveRoute(hash: string, listener: Listener | null): Route {
  const segments = segmentsOf(hash);
  if (segments === null) return NOT_FOUND;
  const route = parse(segments);
  if (LOOPBACK_ONLY.has(route.name) && listener !== 'loopback') return NOT_FOUND;
  return route;
}

export function hrefFor(route: Route): string {
  const encode = encodeURIComponent;
  switch (route.name) {
    case 'cell':
      return `#/cells/${encode(route.id)}`;
    case 'thoughts':
      return `#/thoughts/${encode(route.bee)}`;
    case 'attendant':
      return `#/attendant/${encode(route.principal)}`;
    case 'honey':
      return route.path ? `#/honey/${route.path.split('/').map(encode).join('/')}` : '#/honey';
    case 'not_found':
      return '#/';
    default:
      return `#/${route.name}`;
  }
}
