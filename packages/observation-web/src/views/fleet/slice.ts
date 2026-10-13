/**
 * The Fleet slice: every Cell as one row, seeded by the aggregate and nudged by the Cells stream.
 *
 * `GET /v1/fleet` is the only source of the fields no single Cell frame carries (where the Warden
 * runs, where the models come from, the task, Alarms, Cell Wax, lease age). A `CellFrame` carries
 * the Cell's own fields, so the reducer folds those into the row at once, which keeps the tier,
 * the mask and the status honest between reads, and counts the frame so the view knows to read the
 * aggregate again shortly. The slice is bounded: past `MAX_ROWS` a frame for an unknown Cell adds
 * no row (the next read of the aggregate is authoritative), and a seed is cut to the bound.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Registered in `src/app.tsx`'s table, filled by
 *   `use_fleet.ts` (reads) and `useViewStream` (frames), read by `fleet.tsx`.
 *
 * Key invariants:
 *   - Pure and bounded: at most `MAX_ROWS` rows, one per Cell id.
 *   - A frame always changes the state (it bumps `frames`), because every frame is a reason to
 *     read the aggregate again, even one that changes no field of its row.
 *   - A seed replaces the rows wholesale: the aggregate is the truth, the fold is only haste.
 *   - Rows are the generated types as received; nothing is invented, only defaults for a Cell the
 *     aggregate has not named yet.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1.
 *   - src/views/fleet/fold.ts for how a frame meets a row; src/store/registry.ts, merged into below
 *     so `useSlice('fleet')` is typed.
 */
import type { components } from '../../landing_board/types';
import type { Slice } from '../../store';
import { foldCell, MAX_ROWS } from './fold';

type Schemas = components['schemas'];
export type FleetRow = Schemas['FleetRow'];
export type FleetAnswer = Schemas['FleetView'];
export type TierLegendEntry = Schemas['TierLegendEntry'];
export type CellFrame = Schemas['CellFrame'];

export { MAX_ROWS };

export interface FleetState {
  seeded: boolean;
  rows: readonly FleetRow[];
  // The tier meanings from the aggregate; empty until the first answer.
  legend: readonly TierLegendEntry[];
  // How many Cell frames have arrived; the view reads the aggregate again when it moves.
  frames: number;
  // The last refusal or failure of a read, shown through `ErrorNotice`; null when well.
  error: unknown;
}

export type FleetEvent =
  | { type: 'seeded'; answer: FleetAnswer }
  | { type: 'failed'; error: unknown }
  | { type: 'frame'; frame: CellFrame };

export const initialFleet: FleetState = {
  seeded: false,
  rows: [],
  legend: [],
  frames: 0,
  error: null,
};

export function reduceFleet(state: FleetState, event: FleetEvent): FleetState {
  switch (event.type) {
    case 'seeded':
      return {
        ...state,
        seeded: true,
        rows: event.answer.rows.slice(0, MAX_ROWS),
        legend: event.answer.legend,
        error: null,
      };
    case 'failed':
      return { ...state, error: event.error };
    case 'frame':
      return { ...state, frames: state.frames + 1, rows: foldCell(state.rows, event.frame.cell) };
  }
}

export const fleetSlice: Slice<FleetState, FleetEvent> = {
  initial: initialFleet,
  reduce: reduceFleet,
};

// Tells the store's hooks about this slice, so `useSlice('fleet')` and `dispatch` are typed.
declare module '../../store/registry' {
  interface SliceRegistry {
    fleet: Slice<FleetState, FleetEvent>;
  }
}
