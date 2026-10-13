/**
 * The Cell view's face: the page component and its slice.
 *
 * `src/app.tsx` adds `cellSlice` to its table and the outlet draws `CellPageView`; both take them
 * from here. The component is named `CellPageView` so it never clashes with the schema's `CellView`
 * (the Cell record the page and the Cells stream carry).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/`. Imported by `src/app.tsx` and `src/shell/`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/cell/cell.tsx and src/views/cell/slice.ts.
 */
export { CellPageView } from './cell';
export { cellSlice } from './slice';
export type { CellPageEvent, CellPageState } from './slice';
