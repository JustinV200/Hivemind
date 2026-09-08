/**
 * The Observation Hive's composition root component.
 *
 * The Observation Hive is the Hive's live read-only dashboard: the Queen's (the central
 * orchestrator's) and every bee's thoughts, Cell diagrams, the fleet list and more, described in
 * README.md's phase 12 plan. This file is deliberately a placeholder for roadmap step 0.1: it
 * renders only the Hive's name so `pnpm test` and `pnpm build` have something real to exercise
 * before the routed views, streams and Landing Board client land.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web). Mounted by src/main.tsx. Will grow into the composition root that
 *   wires routes, stream subscriptions and PWA registration once phase 12 begins.
 *
 * Key invariants:
 *   - Renders synchronously with no data fetching, so it never throws before the Landing Board
 *     client (packages/observation-web/src/landing_board/) exists.
 *
 * See Also:
 *   - tests/app.test.tsx for the test that pins this placeholder's output.
 *   - README.md for the phase this package is built in.
 */
import type { ReactElement } from 'react';

export default function App(): ReactElement {
  // Placeholder body: phase 12 replaces this with routed views over live Hive state.
  return <div>HiveMind</div>;
}
