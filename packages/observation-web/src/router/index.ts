/**
 * The router's face: the route table, the resolver and the `useRoute` hook.
 *
 * Importers take everything routing-related from here, so the split between the pure table and
 * the React hook stays an internal matter.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/router/`. Imported by `src/shell/`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/router/routes.ts and src/router/use_route.ts.
 */
export { hrefFor, LOOPBACK_ONLY, NOT_FOUND, resolveRoute } from './routes';
export type { Route, RouteName } from './routes';
export { useRoute } from './use_route';
