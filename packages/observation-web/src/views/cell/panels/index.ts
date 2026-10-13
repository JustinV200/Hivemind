/**
 * The panels' face: every panel beside the Cell diagram.
 *
 * Importers take the panels from here so the file split inside can change without touching the
 * page that arranges them.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Imported by `src/views/cell/cell.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 5.2 for what the page holds.
 */
export { AlarmsPanel } from './alarms_panel';
export { CappingPanel } from './capping_panel';
export { ForagePanel } from './forage_panel';
export { HoneyPanel } from './honey_panel';
export { ModePanel } from './mode_panel';
export { TasksPanel } from './tasks_panel';
export { WaxPanel } from './wax_panel';
