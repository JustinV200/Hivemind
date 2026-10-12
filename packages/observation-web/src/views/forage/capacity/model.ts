/**
 * The capacity model: what each host has, how much of it is in use, granted, reserved and free.
 *
 * A host is a Cell that reported its capacity (the Hive Stand, a Nuc, a Virtual Cell's backend;
 * the schema names the Cell and not its kind, so the Cell id is the host's name). Each host gets
 * rows for cores, memory, GPUs, model seats and sub-bee slots, and the shared pool gets rows for
 * the seats and sub-bee slots the Queen divides and the memory the Royal Reserve holds back. A row
 * is a total and ordered segments (in use, granted, reserve, free); a dimension the report gives
 * only a total for (GPUs, local seats) has no segments and is marked unmeasured, never guessed.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/capacity/`. Built by `forage_screen.tsx` from the
 *   slice; laid out by `layout.ts` and drawn by `diagram.tsx`.
 *
 * Key invariants:
 *   - Pure: the same figures give the same blocks, hosts sorted by Cell id.
 *   - Segments hold positive amounts only; a row's `over` is how far its segments exceed its total
 *     (an over-committed host or an overdrawn reserve), 0 otherwise.
 *   - Nothing is invented: a figure the schema does not carry is not drawn.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.7 and 5.3.
 */
import type { CapacityView, GrantView, HeadroomView, ReserveView } from '../slice';

export type SegmentKind = 'in_use' | 'granted' | 'reserve' | 'free';
export type Unit = 'cores' | 'bytes' | 'count';

export interface Segment {
  kind: SegmentKind;
  amount: number;
}

export interface CapacityRow {
  key: string;
  label: string;
  unit: Unit;
  total: number;
  segments: readonly Segment[];
  // False when the report gives a total only (so no in-use or free figure is drawn).
  measured: boolean;
  over: number;
}

export interface HostBlock {
  id: string;
  title: string;
  rows: readonly CapacityRow[];
}

export interface CapacityInput {
  capacities: readonly CapacityView[];
  grants: readonly GrantView[];
  headroom: HeadroomView | null;
  reserve: ReserveView | null;
}

const round2 = (n: number): number => Math.round(n * 100) / 100;
const sum = (values: readonly number[]): number => values.reduce((a, b) => a + b, 0);
const ORDER: readonly SegmentKind[] = ['in_use', 'granted', 'reserve', 'free'];

// Builds a row from named amounts: keeps the positive ones in the fixed order and totals the excess.
export function makeRow(
  head: Pick<CapacityRow, 'key' | 'label' | 'unit' | 'total'>,
  amounts: Partial<Record<SegmentKind, number>>,
  measured = true,
): CapacityRow {
  const segments = ORDER.flatMap((kind) => {
    const amount = round2(amounts[kind] ?? 0);
    return amount > 0 ? [{ kind, amount }] : [];
  });
  const over = measured ? Math.max(0, round2(sum(segments.map((s) => s.amount)) - head.total)) : 0;
  return { ...head, segments: measured ? segments : [], measured, over };
}

// Cores: the load average over cores says how much of them is busy; above 1 the host is overloaded.
function coreRow(host: CapacityView): CapacityRow {
  const busy = Math.max(0, host.cpu_load) * host.cores;
  return makeRow(
    { key: 'cores', label: 'Cores', unit: 'cores', total: host.cores },
    { in_use: busy, free: host.cores - busy },
  );
}

function memoryRow(host: CapacityView): CapacityRow {
  const free = Math.min(Math.max(0, host.memory_free_bytes), host.memory_bytes);
  return makeRow(
    { key: 'memory', label: 'Memory', unit: 'bytes', total: host.memory_bytes },
    { in_use: host.memory_bytes - free, free },
  );
}

// Sub-bee slots the host can run, against what the live grants on that Cell already let through.
function slotRow(host: CapacityView, grants: readonly GrantView[]): CapacityRow {
  const granted = sum(grants.filter((g) => g.cell_id === host.cell_id).map((g) => g.max_sub_bees));
  return makeRow(
    { key: 'sub_bees', label: 'Sub-bee slots', unit: 'count', total: host.max_sub_bees },
    { granted, free: host.max_sub_bees - granted },
  );
}

function hostBlock(host: CapacityView, grants: readonly GrantView[]): HostBlock {
  const unmeasured = (key: string, label: string, total: number): CapacityRow =>
    makeRow({ key, label, unit: 'count', total }, {}, false);
  return {
    id: host.cell_id,
    title: host.cell_id,
    rows: [
      coreRow(host),
      memoryRow(host),
      unmeasured('gpus', 'GPUs', host.gpus),
      unmeasured('seats', 'Local model seats', host.local_seats),
      slotRow(host, grants),
    ],
  };
}

// The shared pool: the Royal Reserve held back first, then the grants, then what is still free.
function poolRows(input: CapacityInput): CapacityRow[] {
  const { headroom, reserve, grants, capacities } = input;
  const rows: CapacityRow[] = [];
  if (headroom && reserve) {
    const granted = sum(grants.map((g) => g.seats));
    rows.push(
      makeRow(
        {
          key: 'seats',
          label: 'Shared model seats',
          unit: 'count',
          total: reserve.seats + granted + headroom.shared_seats,
        },
        { reserve: reserve.seats, granted, free: headroom.shared_seats },
      ),
    );
  }
  if (headroom) {
    const granted = sum(grants.map((g) => g.max_sub_bees));
    rows.push(
      makeRow(
        {
          key: 'sub_bees',
          label: 'Shared sub-bee slots',
          unit: 'count',
          total: granted + headroom.sub_bees,
        },
        { granted, free: headroom.sub_bees },
      ),
    );
  }
  if (reserve && capacities.length > 0) rows.push(reservedMemory(capacities, reserve));
  return rows;
}

// The reserve is held out of free memory; if it is bigger than what is free the row overdraws.
function reservedMemory(capacities: readonly CapacityView[], reserve: ReserveView): CapacityRow {
  const total = sum(capacities.map((c) => c.memory_bytes));
  const free = sum(capacities.map((c) => Math.min(c.memory_free_bytes, c.memory_bytes)));
  return makeRow(
    { key: 'memory', label: 'Memory across hosts', unit: 'bytes', total },
    {
      in_use: total - free,
      reserve: reserve.memory_bytes,
      free: Math.max(0, free - reserve.memory_bytes),
    },
  );
}

export function hostBlocks(input: CapacityInput): HostBlock[] {
  const pool = poolRows(input);
  const hosts = [...input.capacities]
    .sort((a, b) => (a.cell_id < b.cell_id ? -1 : a.cell_id > b.cell_id ? 1 : 0))
    .map((host) => hostBlock(host, input.grants));
  return pool.length > 0
    ? [{ id: 'shared-pool', title: 'Shared pool', rows: pool }, ...hosts]
    : hosts;
}
