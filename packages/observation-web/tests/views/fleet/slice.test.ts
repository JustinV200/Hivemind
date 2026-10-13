/**
 * Tests for the Fleet reducer: the seed, folding a Cell frame into a row, and the bound.
 *
 * The slice is pure, so each rule is checked on the reducer alone: a seed replaces the rows, a
 * frame updates the Cell's own fields at once and leaves the aggregate's fields alone, a frame for
 * a Cell the list has not seen adds a row, and the list never grows past its bound.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/fleet/slice.ts and fold.ts.
 *
 * Key invariants:
 *   - Frames are built with `cellFrame` and rows with `fleetRow`; only the fields a test is about
 *     are set.
 *
 * See Also:
 *   - tests/views/fleet/fleet_view.test.tsx for the view over this slice.
 */
import { describe, expect, it } from 'vitest';
import { initialFleet, MAX_ROWS, reduceFleet } from '../../../src/views/fleet/slice';
import type { FleetEvent, FleetState } from '../../../src/views/fleet/slice';
import { cellFrame } from '../../support/frames';
import { fleetAnswer, fleetRow, LEGEND } from './support';

const run = (events: FleetEvent[], from: FleetState = initialFleet): FleetState =>
  events.reduce(reduceFleet, from);
const seeded = (...rows: ReturnType<typeof fleetRow>[]): FleetEvent => ({
  type: 'seeded',
  answer: fleetAnswer(rows),
});
const frame = (cell: Parameters<typeof cellFrame>[0]): FleetEvent => ({
  type: 'frame',
  frame: cellFrame(cell),
});

describe('seed', () => {
  it('lands the rows and the legend and clears an earlier error', () => {
    const failed = run([{ type: 'failed', error: new Error('x') }]);
    const state = run([seeded(fleetRow({ id: 'a' }), fleetRow({ id: 'b' }))], failed);
    expect(state.rows.map((r) => r.id)).toEqual(['a', 'b']);
    expect(state.legend).toEqual(LEGEND);
    expect(state).toMatchObject({ seeded: true, error: null });
  });

  it('replaces the rows wholesale, so a Cell the aggregate dropped is gone', () => {
    const state = run([seeded(fleetRow({ id: 'a' })), seeded(fleetRow({ id: 'b' }))]);
    expect(state.rows.map((r) => r.id)).toEqual(['b']);
  });

  it('is cut to the bound', () => {
    const rows = Array.from({ length: MAX_ROWS + 5 }, (_, i) => fleetRow({ id: `c${i}` }));
    expect(run([{ type: 'seeded', answer: fleetAnswer(rows) }]).rows).toHaveLength(MAX_ROWS);
  });
});

describe('a Cell frame', () => {
  const held = fleetRow({
    id: 'cell_1',
    comb_shield: 'MEADOW',
    mask_state: null,
    mode: 'ACTIVE',
    kind: 'REAL',
    warden_runs: 'on_cell',
    models_from: 'hybrid',
    open_alarms: 2,
    highest_wax_severity: 'BLOCK',
    current_task_id: 'task_9',
    current_task_role: 'FORAGER',
    status: 'ACTIVE',
    lease_age_s: 120,
  });

  it("folds the Cell's own fields into its row at once", () => {
    const state = run(
      [
        frame({
          id: 'cell_1',
          comb_shield: 'NIGHT_VEIL',
          mask_state: 'QUEEN_FORCED',
          mode: 'WATCH',
          access_level: 'FULL',
          kind: 'REAL',
          lease_state: 'OPEN',
        }),
      ],
      run([seeded(held)]),
    );
    expect(state.rows[0]).toMatchObject({
      comb_shield: 'NIGHT_VEIL',
      mask_state: 'QUEEN_FORCED',
      mode: 'WATCH',
      access_level: 'FULL',
      lease_age_s: 120,
    });
  });

  it('leaves what only the aggregate knows as the aggregate gave it, and counts the frame', () => {
    const state = run([frame({ id: 'cell_1', mode: 'WATCH' })], run([seeded(held)]));
    expect(state.rows[0]).toMatchObject({
      warden_runs: 'on_cell',
      models_from: 'hybrid',
      open_alarms: 2,
      highest_wax_severity: 'BLOCK',
      current_task_id: 'task_9',
      current_task_role: 'FORAGER',
    });
    expect(state.frames).toBe(1);
  });

  it('keeps the list as it was when the frame says nothing new, but still counts it', () => {
    const quiet = { ...held, lease_age_s: null };
    const base = run([seeded(quiet)]);
    const same = frame({
      id: 'cell_1',
      kind: 'REAL',
      comb_shield: 'MEADOW',
      access_level: 'SCRATCH',
      mode: 'ACTIVE',
      name: 'cell',
      source: 'local',
      virtual_status: null,
    });
    const state = run([same], base);
    // The row's status is Warden-derived (ACTIVE), and a Cell with no status words leaves it.
    expect(state.rows).toBe(base.rows);
    expect(state.frames).toBe(1);
  });

  it('shows a Virtual status over anything, and a lease state only over a Cell-derived one', () => {
    const virtual = run(
      [frame({ id: 'v', virtual_status: 'DESTROYING' })],
      run([seeded(fleetRow({ id: 'v', status: 'READY' }))]),
    );
    expect(virtual.rows[0]!.status).toBe('DESTROYING');
    const leased = run(
      [frame({ id: 'r', kind: 'REAL', lease_state: 'RELEASED', virtual_status: null })],
      run([seeded(fleetRow({ id: 'r', kind: 'REAL', status: 'OPEN' }))]),
    );
    expect(leased.rows[0]!.status).toBe('RELEASED');
    const warden = run(
      [frame({ id: 'w', kind: 'REAL', lease_state: 'RELEASED', virtual_status: null })],
      run([seeded(fleetRow({ id: 'w', kind: 'REAL', status: 'ACTIVE' }))]),
    );
    expect(warden.rows[0]!.status).toBe('ACTIVE');
  });

  it('drops the lease age once the lease is no longer open', () => {
    const state = run(
      [frame({ id: 'cell_1', kind: 'REAL', lease_state: 'RELEASED' })],
      run([seeded(held)]),
    );
    expect(state.rows[0]!.lease_age_s).toBeNull();
  });

  it('adds a row for a Cell the list has not seen, with plain defaults', () => {
    const state = run(
      [
        frame({
          id: 'cell_new',
          comb_shield: 'PROPOLIS',
          warden_id: 'warden_1',
          current_tasks: ['task_1'],
          mask_state: 'WARDEN',
        }),
      ],
      run([seeded(held)]),
    );
    expect(state.rows.map((r) => r.id)).toEqual(['cell_1', 'cell_new']);
    expect(state.rows[1]).toMatchObject({
      comb_shield: 'PROPOLIS',
      mask_state: 'WARDEN',
      open_alarms: 0,
      highest_wax_severity: null,
      models_from: 'unknown',
      warden_runs: null,
      current_task_id: 'task_1',
      status: 'READY',
      links: {
        cell_id: 'cell_new',
        attendant_principal: 'warden_1',
        thought_principals: ['warden_1'],
      },
    });
  });

  it('adds no row once the list is full, but still counts the frame', () => {
    const rows = Array.from({ length: MAX_ROWS }, (_, i) => fleetRow({ id: `c${i}` }));
    const state = run(
      [frame({ id: 'extra' })],
      run([{ type: 'seeded', answer: fleetAnswer(rows) }]),
    );
    expect(state.rows).toHaveLength(MAX_ROWS);
    expect(state.rows.some((r) => r.id === 'extra')).toBe(false);
    expect(state.frames).toBe(1);
  });
});

describe('failure', () => {
  it('is held for the view, and a later seed clears it', () => {
    const error = new Error('refused');
    const failed = run([{ type: 'failed', error }]);
    expect(failed.error).toBe(error);
    expect(run([seeded()], failed).error).toBeNull();
  });
});
