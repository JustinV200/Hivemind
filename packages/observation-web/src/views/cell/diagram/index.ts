/**
 * The diagram package's face: the Cell diagram and the pure pieces its tests reach.
 *
 * Importers take the component from here; the model and layout are exported for the tests that
 * check words and geometry without React.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Imported by `src/views/cell/cell.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/cell/diagram/cell_diagram.tsx for the component.
 */
export { CellDiagram } from './cell_diagram';
export { layoutDiagram } from './layout';
export type { Layout, LayoutMode } from './layout';
export { awakeSet, buildModel } from './model';
export type { BeeNode, DiagramModel } from './model';
