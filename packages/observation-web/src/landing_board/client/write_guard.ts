/**
 * The allow-list of request paths the Observation Hive may send with a method other than GET.
 *
 * Codingrules 8.11 gives the UI one write path: a message into the Queen's inbox. The Entrance's
 * own routes (login, device and door management) are the other writes a client of the Landing
 * Board needs. Everything else, an isolate or a lift of a Cell for instance, must be impossible
 * from here, not merely unused. The HTTP wrapper checks every non-GET request against this list
 * before it signs or sends anything, so the guarantee is code, and a test enumerates the
 * document's mutating routes against it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Read by `http.ts`; asserted by
 *   `tests/shell/write_routes.test.ts`.
 *
 * Key invariants:
 *   - Patterns are matched against the path without its query. `*` matches any run of
 *     characters, slashes included.
 *   - A path outside the list is refused with `hivemind.client.write_refused` and nothing is sent.
 *   - Adding a pattern is a design change: it needs an entry in design.md and a reason in review.
 *
 * See Also:
 *   - .claude/codingrules.md section 8.11, the one write path.
 *   - tests/shell/write_routes.test.ts for the route-table test.
 */
import { CLIENT_CODES, clientError } from './errors';

export const WRITE_PATTERNS: readonly string[] = [
  '/v1/auth/*',
  '/v1/chat',
  '/v1/chat/audio',
  '/v1/inbox/*/answer',
  '/v1/inbox/*/acknowledge',
  '/v1/goals*',
  '/v1/push/subscriptions*',
  '/v1/entrance/*',
  '/v1/devices/*',
];

// Compiled once: escape regex characters, then let `*` stand for any run of characters.
const WRITE_MATCHERS: readonly RegExp[] = WRITE_PATTERNS.map(
  (pattern) =>
    new RegExp('^' + pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$'),
);

export function isWriteAllowed(path: string): boolean {
  return WRITE_MATCHERS.some((matcher) => matcher.test(path));
}

export function assertMethodAllowed(method: string, path: string): void {
  if (method.toUpperCase() === 'GET') return;
  if (!isWriteAllowed(path)) {
    throw clientError(CLIENT_CODES.writeRefused, `${method.toUpperCase()} ${path} is not allowed`);
  }
}
