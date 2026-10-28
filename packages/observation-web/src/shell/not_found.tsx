/**
 * `NotFound`: the plain page an unknown route, or a loopback-only screen off the Hive Stand, gets.
 *
 * It says only that nothing is here and offers the way back to the fleet. It does not say whether
 * the screen exists on another listener: the remote listener answers those routes with a 404 and
 * the UI matches it (design.md 5.10).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `outlet.tsx` for `not_found`.
 *
 * Key invariants:
 *   - The heading is "Not found", which the route and gate tests rely on.
 *   - The link is a hash route, so it goes through the router like any nav link.
 *
 * See Also:
 *   - src/router/routes.ts for what resolves to `not_found`.
 */
import type { ReactElement } from 'react';
import { EmptyState, ViewTitle } from '../components';

export function NotFound(): ReactElement {
  return (
    <>
      <ViewTitle title="Not found" />
      <EmptyState>
        There is nothing at this address. <a href="#/fleet">Back to the Fleet</a>
      </EmptyState>
    </>
  );
}
