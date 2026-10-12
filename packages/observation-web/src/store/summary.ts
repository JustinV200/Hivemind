/**
 * The summary slice: the Hive's core counters, seeded once and kept live by three streams.
 *
 * The persistent strip shows active bees, active and inactive Cells, Real and Virtual Cells, and
 * how many bees are in awake (LLM) episodes versus on autopilot. The slice starts from the
 * summary read once (`GET /v1/summary`), then folds frames from the cells, telemetry and episodes
 * streams into one entry per Cell and one per bee, and derives the counters from those entries.
 * The seed supplies each group of counters until the streams have shown at least as many entities
 * as the seed counted; after that the streams are authoritative, so a number never jumps back to
 * a stale seed when a bee finishes.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Folded by `HiveStore`; fed by `feed.ts`; read by
 *   the summary strip in `src/shell/`.
 *
 * Key invariants:
 *   - Pure and bounded: one entry per Cell, and at most `MAX_BEES` bee entries (a finished bee's
 *     entry is removed, and the one with the oldest sample is dropped past the cap), so memory
 *     cannot grow with time.
 *   - A Cell is active when a bee can work in it now: a Real Cell with an OPEN lease, or a
 *     Virtual Cell that is READY or GRANTED. Every other Cell counts as inactive.
 *   - A bee is active until it reports a terminal state (a Worker DONE, FAILED or KILLED; a
 *     Warden OFFLINE or STOPPED).
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 4 for the strip.
 *   - docs/adr/0046-observation-hive-dashboard-stack.md for the seeded-then-live rule.
 */
import type { components } from '../landing_board/types';

type CellFrame = components['schemas']['CellFrame'];
type TelemetryFrame = components['schemas']['TelemetryFrame'];
type EpisodeFrame = components['schemas']['EpisodeFrame'];

// The seed is the document's SummaryView, read once from /v1/summary; the strip shows the eight
// counters the streams can keep live, so a seed's other figures are read but never re-derived.
export type SummarySeed = components['schemas']['SummaryView'];
export type SummaryCounters = Pick<
  SummarySeed,
  | 'active_bees'
  | 'cells_active'
  | 'cells_inactive'
  | 'cells_real'
  | 'cells_virtual'
  | 'bees_awake'
  | 'bees_autopilot'
  | 'as_of'
>;

export const MAX_BEES = 2048;

const ENDED_WORKER = new Set(['DONE', 'FAILED', 'KILLED']);
const ENDED_WARDEN = new Set(['OFFLINE', 'STOPPED']);

interface CellEntry {
  real: boolean;
  active: boolean;
}

export interface SummarySlice {
  seed: SummarySeed | null;
  cells: Record<string, CellEntry>;
  // Bee id to its latest sample time; present only while the bee is active.
  bees: Record<string, string>;
  // Principal to its latest episode: whether it was autopilot handling, and when.
  autopilot: Record<string, { auto: boolean; at: string }>;
  // Once the streams cover the seed's count, each group stops reading the seed.
  streamed: { cells: boolean; bees: boolean; thinking: boolean };
  asOf: string | null;
}

export type SummaryEvent =
  | { type: 'seed'; counters: SummarySeed }
  | { type: 'cell'; frame: CellFrame }
  | { type: 'telemetry'; frame: TelemetryFrame }
  | { type: 'episode'; frame: EpisodeFrame };

export const initialSummary: SummarySlice = {
  seed: null,
  cells: {},
  bees: {},
  autopilot: {},
  streamed: { cells: false, bees: false, thinking: false },
  asOf: null,
};

function isActiveCell(cell: CellFrame['cell']): boolean {
  if (cell.kind === 'REAL') return cell.lease_state === 'OPEN';
  return cell.virtual_status === 'READY' || cell.virtual_status === 'GRANTED';
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const rest = { ...record };
  delete rest[key];
  return rest;
}

// Sets one entry, and when that pushes the record past its cap drops the entry whose time is
// oldest. The cap check costs nothing until it is reached; ISO times compare as strings.
function put<T>(
  record: Record<string, T>,
  key: string,
  value: T,
  timeOf: (entry: T) => string,
): Record<string, T> {
  const next = { ...record, [key]: value };
  const keys = Object.keys(next);
  if (keys.length <= MAX_BEES) return next;
  const oldest = keys.reduce((a, b) => (timeOf(next[b] as T) < timeOf(next[a] as T) ? b : a));
  return without(next, oldest);
}

// The later of two ISO times, treating null as absent.
function latest(a: string | null, b: string | null): string | null {
  if (a === null) return b;
  return b !== null && b > a ? b : a;
}

function foldTelemetry(slice: SummarySlice, frame: TelemetryFrame): SummarySlice {
  const { sample } = frame;
  const bee = sample.worker_id ?? sample.warden_id;
  const ended = sample.worker_id
    ? ENDED_WORKER.has(sample.worker_state ?? '')
    : ENDED_WARDEN.has(sample.warden_state ?? '');
  if (ended) {
    const autopilot = without(slice.autopilot, bee);
    return {
      ...slice,
      bees: without(slice.bees, bee),
      autopilot,
      asOf: latest(slice.asOf, sample.at),
    };
  }
  const bees = put(slice.bees, bee, sample.at, (at) => at);
  return { ...slice, bees, asOf: latest(slice.asOf, sample.at) };
}

function foldEpisode(slice: SummarySlice, frame: EpisodeFrame): SummarySlice {
  const { episode } = frame;
  const entry = { auto: episode.is_autopilot, at: episode.at };
  const autopilot = put(slice.autopilot, episode.principal, entry, (e) => e.at);
  return { ...slice, autopilot, asOf: latest(slice.asOf, episode.at) };
}

function foldCell(slice: SummarySlice, frame: CellFrame): SummarySlice {
  const { cell } = frame;
  const entry: CellEntry = { real: cell.kind === 'REAL', active: isActiveCell(cell) };
  const current = slice.cells[cell.id];
  if (current && current.real === entry.real && current.active === entry.active) return slice;
  // A Cell with no event time yet still counts as data having arrived (an empty string is not null).
  const asOf = latest(slice.asOf, cell.last_event_at) ?? '';
  return { ...slice, cells: { ...slice.cells, [cell.id]: entry }, asOf };
}

// Marks a group as streamed once the streams show at least as many entities as the seed counted.
function settle(slice: SummarySlice): SummarySlice {
  const { seed } = slice;
  if (!seed) return slice;
  const streamed = {
    cells:
      slice.streamed.cells ||
      Object.keys(slice.cells).length >= seed.cells_active + seed.cells_inactive,
    bees: slice.streamed.bees || Object.keys(slice.bees).length >= seed.active_bees,
    thinking:
      slice.streamed.thinking ||
      Object.keys(slice.autopilot).length >= seed.bees_awake + seed.bees_autopilot,
  };
  const same =
    streamed.cells === slice.streamed.cells &&
    streamed.bees === slice.streamed.bees &&
    streamed.thinking === slice.streamed.thinking;
  return same ? slice : { ...slice, streamed };
}

export function reduceSummary(slice: SummarySlice, event: SummaryEvent): SummarySlice {
  switch (event.type) {
    case 'seed':
      return settle({
        ...slice,
        seed: event.counters,
        asOf: latest(slice.asOf, event.counters.as_of),
      });
    case 'cell':
      return settle(foldCell(slice, event.frame));
    case 'telemetry':
      return settle(foldTelemetry(slice, event.frame));
    case 'episode':
      return settle(foldEpisode(slice, event.frame));
  }
}

// The counters the strip shows, or null while there is nothing to show yet.
export function summaryCounters(slice: SummarySlice): SummaryCounters | null {
  const { seed, streamed } = slice;
  // Nothing to show until the seed or any frame has arrived; after that zeros are real zeros.
  if (!seed && slice.asOf === null) return null;
  const cells = Object.values(slice.cells);
  const useSeedCells = seed !== null && !streamed.cells;
  const useSeedThinking = seed !== null && !streamed.thinking;
  const awake = Object.values(slice.autopilot).filter((entry) => !entry.auto).length;
  return {
    active_bees: seed && !streamed.bees ? seed.active_bees : Object.keys(slice.bees).length,
    cells_active: useSeedCells ? seed.cells_active : cells.filter((c) => c.active).length,
    cells_inactive: useSeedCells ? seed.cells_inactive : cells.filter((c) => !c.active).length,
    cells_real: useSeedCells ? seed.cells_real : cells.filter((c) => c.real).length,
    cells_virtual: useSeedCells ? seed.cells_virtual : cells.filter((c) => !c.real).length,
    bees_awake: useSeedThinking ? seed.bees_awake : awake,
    bees_autopilot: useSeedThinking
      ? seed.bees_autopilot
      : Object.keys(slice.autopilot).length - awake,
    as_of: slice.asOf ?? seed?.as_of ?? '',
  };
}
