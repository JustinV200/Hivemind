/**
 * The task graph's face: the component and the pure pieces a test reaches for.
 *
 * `attendant.tsx` takes the graph from here; the layout is exported so a test can check the
 * geometry without drawing it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Imported by `attendant.tsx` and
 *   `lists/task_lists.tsx`, and by the tests.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/attendant/graph/layout.ts and task_graph.tsx.
 */
export { layoutGraph, layersOf } from './layout';
export type { GraphEdge, GraphLayout, GraphMode, PlacedNode } from './layout';
export { describeNode, hrefOfNode, placementOf, statusWord, toneOf } from './status';
export type { TaskNode, TaskStatus, Tone } from './status';
export { TaskGraph } from './task_graph';
