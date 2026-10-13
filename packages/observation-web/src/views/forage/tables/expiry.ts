/**
 * The expiry rule: which grants are nearing the end of their lease, and in what order to list them.
 *
 * A grant is a lease with an `expires_at`, renewed on its Warden's Heartbeat. One whose lease ends
 * within ten minutes (or has already passed without the Queen dropping it) is "nearing expiry": the
 * table lists those first, soonest first, and the diagram marks them with the warn token. The rest
 * keep the ledger's order (holder, then id), so the list under a reader's eyes only moves when a
 * lease really nears its end.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/tables/`. Used by the grants table and by
 *   `flow/model.ts`; the current time comes in as an argument, never from a clock.
 *
 * Key invariants:
 *   - Pure: the same grants and time give the same order.
 *   - The window is exactly `NEAR_EXPIRY_MS`; a grant on its boundary counts as near.
 *   - An unparsable time is treated as near, so a malformed lease is never hidden.
 *
 * See Also:
 *   - docs/observation/design.md section 5.3 (grants nearing expiry).
 */
import type { GrantView } from '../slice';

export const NEAR_EXPIRY_MS = 10 * 60_000; // Ten minutes, as the roadmap's 12.5 asks.

export function isNearExpiry(expiresAt: string, nowMs: number): boolean {
  const at = Date.parse(expiresAt);
  return Number.isNaN(at) || at - nowMs <= NEAR_EXPIRY_MS;
}

// Near-expiry grants first (soonest first, ties by id), then the others as given.
export function orderGrants(grants: readonly GrantView[], nowMs: number): GrantView[] {
  const near = grants
    .filter((g) => isNearExpiry(g.expires_at, nowMs))
    .sort((a, b) => Date.parse(a.expires_at) - Date.parse(b.expires_at) || (a.id < b.id ? -1 : 1));
  const rest = grants.filter((g) => !isNearExpiry(g.expires_at, nowMs));
  return [...near, ...rest];
}
