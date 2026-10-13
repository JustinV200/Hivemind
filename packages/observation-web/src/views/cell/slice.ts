/**
 * The Cell page slice: one Cell's page, kept live by its three streams.
 *
 * The view seeds the slice from `GET /v1/cells/{cell_id}/page`, then folds frames into it at once:
 * a `CellFrame` for this Cell replaces the page's Cell (tier, mask, mode, lease, current tasks);
 * a telemetry sample from this Cell's Warden updates the bee it describes (status, current task,
 * context gauge, spend), or adds a row for a sub-bee the page did not list yet. A trail event that
 * names this Cell or its Warden only bumps a counter: the Alarms, Cell Wax and Capping lists have
 * no stream of their own, so the hook re-reads the page when the counter moves. Nothing here reads.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/`. Registered in `src/app.tsx`'s table, filled by
 *   `use_cell.ts` (reads) and `useViewStream` (frames), read by the page and its panels.
 *
 * Key invariants:
 *   - Pure and bounded: at most `MAX_BEES` bee rows; no clock, no I/O.
 *   - Frames for another Cell, or from another Cell's Warden, return the state unchanged (same
 *     identity), so the store stays quiet and nothing re-renders.
 *   - Nothing is folded before the seed lands: the page is the only source of the Warden's id,
 *     which is what tells this Cell's telemetry from the rest of the Hive's.
 *   - An answer for a Cell the reader has already left is dropped.
 *   - Stored records are the generated types as received; only the bee rows' live fields change.
 *
 * See Also:
 *   - docs/observation/design.md section 5.2.
 *   - src/store/registry.ts, merged into below so `useSlice('cell')` is typed.
 */
import type { components } from '../../landing_board/types';
import type { Slice } from '../../store';

type Schemas = components['schemas'];
export type CellPage = Schemas['CellPageView'];
export type BeeRow = Schemas['BeeRow'];
export type CellFrame = Schemas['CellFrame'];
export type TelemetryFrame = Schemas['TelemetryFrame'];
export type TrailFrame = Schemas['TrailFrame'];

// Far more than a Cell can carry (a Cell's bee slots are in the tens); it only bounds a runaway.
export const MAX_BEES = 500;

export interface CellPageState {
  // Which Cell the page is for; null until a view opens one.
  cellId: string | null;
  page: CellPage | null;
  // The last refusal or failure of a read, shown through `ErrorNotice`; null when well.
  error: unknown;
  // How many trail events named this Cell or its Warden since it was opened; the hook re-reads
  // the page when this moves, so the reducer stays free of timers and requests.
  trailTicks: number;
}

export type CellPageEvent =
  | { type: 'open'; cellId: string }
  | { type: 'seeded'; cellId: string; page: CellPage }
  | { type: 'failed'; cellId: string; error: unknown }
  | { type: 'cell'; frame: CellFrame }
  | { type: 'telemetry'; frame: TelemetryFrame }
  | { type: 'trail'; frame: TrailFrame };

export const initialCellPage: CellPageState = {
  cellId: null,
  page: null,
  error: null,
  trailTicks: 0,
};

// Used over window, 0..1; null while either is unknown, so the gauge says "not reported".
function fractionOf(used: number | null, window: number | null): number | null {
  return used !== null && window !== null && window > 0 ? used / window : null;
}

// A sub-bee the page did not list: its role is only known from a task, which the sample lacks.
function newRow(id: string): BeeRow {
  return {
    id,
    role: 'UNKNOWN',
    status: 'UNKNOWN',
    task_id: null,
    context_fraction: null,
    context_tokens_used: null,
    context_window: null,
    spend_usd: null,
  };
}

function applySample(row: BeeRow, sample: TelemetryFrame['sample']): BeeRow {
  // The Warden's own report carries its Warden state; a sub-bee's carries its Worker state.
  const status = sample.worker_id === null ? sample.warden_state : sample.worker_state;
  return {
    ...row,
    status: status ?? row.status,
    task_id: sample.task_id,
    context_tokens_used: sample.tokens_used,
    context_window: sample.context_window,
    context_fraction: fractionOf(sample.tokens_used, sample.context_window),
    spend_usd: sample.spend,
  };
}

function foldTelemetry(state: CellPageState, frame: TelemetryFrame): CellPageState {
  const { page } = state;
  const { sample } = frame;
  // Another Cell's Warden (or no Warden here): not this page's bees.
  if (!page || page.cell.warden_id === null || sample.warden_id !== page.cell.warden_id) {
    return state;
  }
  const id = sample.worker_id ?? sample.warden_id;
  const known = page.bees.some((bee) => bee.id === id);
  // The bound only stops new rows; a known bee always updates.
  if (!known && page.bees.length >= MAX_BEES) return state;
  const bees = known
    ? page.bees.map((bee) => (bee.id === id ? applySample(bee, sample) : bee))
    : [...page.bees, applySample(newRow(id), sample)];
  return { ...state, page: { ...page, bees } };
}

function foldCell(state: CellPageState, frame: CellFrame): CellPageState {
  if (!state.page || frame.cell.id !== state.cellId) return state;
  return { ...state, page: { ...state.page, cell: frame.cell } };
}

// True when the event names this Cell or its Warden as its subject, actor or payload target.
function namesThisCell(state: CellPageState, event: TrailFrame['event']): boolean {
  const mine = [state.cellId, state.page?.cell.warden_id].filter(
    (id): id is string => typeof id === 'string',
  );
  const named = [
    event.subject_id,
    event.actor,
    event.payload['cell_id'],
    event.payload['warden_id'],
  ];
  return named.some((id) => typeof id === 'string' && mine.includes(id));
}

function foldTrail(state: CellPageState, frame: TrailFrame): CellPageState {
  if (!state.page || !namesThisCell(state, frame.event)) return state;
  return { ...state, trailTicks: state.trailTicks + 1 };
}

export function reduceCellPage(state: CellPageState, event: CellPageEvent): CellPageState {
  switch (event.type) {
    case 'open':
      // Every visit starts clean, so a Cell revisited never shows what it was when last seen.
      return { ...initialCellPage, cellId: event.cellId };
    case 'seeded':
      // The page is authoritative: it replaces whatever frames folded since the last read.
      return event.cellId === state.cellId ? { ...state, page: event.page, error: null } : state;
    case 'failed':
      return event.cellId === state.cellId ? { ...state, error: event.error } : state;
    case 'cell':
      return foldCell(state, event.frame);
    case 'telemetry':
      return foldTelemetry(state, event.frame);
    case 'trail':
      return foldTrail(state, event.frame);
  }
}

export const cellSlice: Slice<CellPageState, CellPageEvent> = {
  initial: initialCellPage,
  reduce: reduceCellPage,
};

// Tells the store's hooks about this slice, so `useSlice('cell')` and `dispatch` are typed.
declare module '../../store/registry' {
  interface SliceRegistry {
    cell: Slice<CellPageState, CellPageEvent>;
  }
}
