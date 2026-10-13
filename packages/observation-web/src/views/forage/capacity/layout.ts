/**
 * The capacity layout: pure positions and widths for the per-host stacked bars.
 *
 * Every row is drawn on a fixed 360 unit wide canvas (the design starts at 360 px, so a 12 unit
 * label is a 12 px label on a phone and the `viewBox` scales it up on a wide screen). A row is a
 * label line, a bar and a figures line; a host is a heading and its rows. Widths come from the
 * amounts alone: each segment's share of the larger of the total and the sum of the segments, so
 * an over-committed row still fits on its bar and shows where the total ends. Nothing here reads a
 * clock, a random number or the DOM, so two layouts of the same blocks are equal.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/capacity/`. Takes the blocks of `model.ts`; its
 *   output is drawn by `diagram.tsx` and checked by tests as plain numbers.
 *
 * Key invariants:
 *   - Pure and deterministic; rounded to two decimals so output is stable across engines.
 *   - Segment widths sum to at most `BAR_WIDTH`; a zero-capacity row has no segments at all.
 *   - `limitX` is set only when a row is over-committed: where its total ends on the bar.
 *
 * See Also:
 *   - docs/observation/design.md section 4.8 for the diagram rules.
 */
import { bytes, figure } from '../format';
import type { CapacityRow, HostBlock, SegmentKind, Unit } from './model';

export const VIEW_WIDTH = 360;
export const BAR_X = 8;
export const BAR_WIDTH = 344;
export const BAR_HEIGHT = 12;
const HEAD_HEIGHT = 24;
const ROW_HEIGHT = 56; // label line, bar, figures line, and breathing space.
const BLOCK_GAP = 16;

export interface LaidSegment {
  kind: SegmentKind;
  amount: number;
  x: number;
  width: number;
}

export interface LaidRow {
  row: CapacityRow;
  y: number; // Top of the row; the label baseline and the bar are offsets from it.
  segments: LaidSegment[];
  limitX: number | null;
  figures: string;
}

export interface LaidBlock {
  id: string;
  title: string;
  y: number;
  rows: LaidRow[];
}

export interface CapacityLayout {
  width: number;
  height: number;
  blocks: LaidBlock[];
}

const round2 = (n: number): number => Math.round(n * 100) / 100;

export const KIND_WORD: Readonly<Record<SegmentKind, string>> = {
  in_use: 'in use',
  granted: 'granted',
  reserve: 'reserve',
  free: 'free',
};

const amountText = (unit: Unit, n: number): string => (unit === 'bytes' ? bytes(n) : figure(n));

// The figures line: every segment but the free one, then the total; or why there is nothing.
export function figuresOf(row: CapacityRow): string {
  const total = amountText(row.unit, row.total);
  if (row.total <= 0) return 'none reported';
  if (!row.measured) return `${total}, use not reported`;
  const parts = row.segments
    .filter((s) => s.kind !== 'free')
    .map((s) => `${amountText(row.unit, s.amount)} ${KIND_WORD[s.kind]}`);
  const lead = parts.length > 0 ? parts.join(', ') : 'all free';
  return `${lead} of ${total}${row.over > 0 ? ', over by ' + amountText(row.unit, row.over) : ''}`;
}

// Segment x and width: shares of the larger of the total and the segments' sum; none for no total.
export function layoutSegments(row: CapacityRow): {
  segments: LaidSegment[];
  limitX: number | null;
} {
  const used = row.segments.reduce((a, s) => a + s.amount, 0);
  const span = Math.max(row.total, used);
  if (row.total <= 0 || span <= 0) return { segments: [], limitX: null };
  let x = BAR_X;
  // Walk the segments left to right, each starting where the previous one ended.
  const segments = row.segments.map((s) => {
    const width = round2((s.amount / span) * BAR_WIDTH);
    const laid = { kind: s.kind, amount: s.amount, x: round2(x), width };
    x += width;
    return laid;
  });
  const limitX = row.over > 0 ? round2(BAR_X + (row.total / span) * BAR_WIDTH) : null;
  return { segments, limitX };
}

function layoutRow(row: CapacityRow, y: number): LaidRow {
  return { row, y, ...layoutSegments(row), figures: figuresOf(row) };
}

export function layoutCapacity(blocks: readonly HostBlock[]): CapacityLayout {
  let y = 0;
  // Stack the hosts top to bottom: a heading, then its rows, then a gap before the next host.
  const laid = blocks.map((block) => {
    const top = y;
    const rows = block.rows.map((row, i) => layoutRow(row, top + HEAD_HEIGHT + i * ROW_HEIGHT));
    y = top + HEAD_HEIGHT + block.rows.length * ROW_HEIGHT + BLOCK_GAP;
    return { id: block.id, title: block.title, y: top, rows };
  });
  return { width: VIEW_WIDTH, height: Math.max(0, y - BLOCK_GAP), blocks: laid };
}
