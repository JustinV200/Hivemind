/**
 * Tests for the Forage tables' pure rules: expiry order, the map rows and their sort, spend sums.
 *
 * These are the decisions behind the tables, checked without rendering: which grants are "nearing
 * expiry" and in what order they are listed, which sources the Forage map holds (a provider no
 * slot binds still has a row), a stable total sort on any column in either direction, and the
 * per-Warden spend totals the gauges draw.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/forage/tables/.
 *
 * Key invariants:
 *   - "Now" is fixed in each test.
 *
 * See Also:
 *   - tests/views/forage/forage_view.test.tsx for the tables as rendered.
 */
import { describe, expect, it } from 'vitest';
import { isNearExpiry, NEAR_EXPIRY_MS, orderGrants } from '../../../src/views/forage/tables/expiry';
import { mapRows, NONE, sortMapRows } from '../../../src/views/forage/tables/map_rows';
import { spendByWarden } from '../../../src/views/forage/tables/spend_gauges';
import { grant, llm } from './support';

const NOW = Date.parse('2026-01-01T00:00:00Z');
const at = (minutes: number): string => new Date(NOW + minutes * 60_000).toISOString();

describe('expiry', () => {
  it('is ten minutes, and the boundary counts as near', () => {
    expect(NEAR_EXPIRY_MS).toBe(600_000);
    expect(isNearExpiry(at(10), NOW)).toBe(true);
    expect(isNearExpiry(at(10.01), NOW)).toBe(false);
    expect(isNearExpiry(at(-3), NOW)).toBe(true);
    expect(isNearExpiry('not a time', NOW)).toBe(true);
  });

  it('lists near-expiry grants first, soonest first, then the rest as given', () => {
    const ordered = orderGrants(
      [
        grant({ id: 'g_a', expires_at: at(90) }),
        grant({ id: 'g_b', expires_at: at(8) }),
        grant({ id: 'g_c', expires_at: at(30) }),
        grant({ id: 'g_d', expires_at: at(2) }),
      ],
      NOW,
    );
    expect(ordered.map((g) => g.id)).toEqual(['g_d', 'g_b', 'g_a', 'g_c']);
  });

  it('breaks a tie on the grant id', () => {
    const ordered = orderGrants(
      [grant({ id: 'g_2', expires_at: at(5) }), grant({ id: 'g_1', expires_at: at(5) })],
      NOW,
    );
    expect(ordered.map((g) => g.id)).toEqual(['g_1', 'g_2']);
  });
});

describe('the Forage map', () => {
  it('has a row per binding, with its provider kind and health', () => {
    const rows = mapRows(llm());
    expect(rows.find((r) => r.key === 'queen')).toMatchObject({
      model: 'big-1',
      provider: 'cloud',
      kind: 'anthropic',
      health: 'HEALTHY',
      serves: 'queen',
      fallback: NONE,
    });
    expect(rows.find((r) => r.key === 'worker')?.fallback).toBe('queen');
  });

  it('also lists a provider no binding uses', () => {
    const rows = mapRows(
      llm({
        providers: [
          ...llm().providers,
          { clustered: false, failed_probes: 0, health: 'DOWN', kind: 'fake', name: 'spare' },
        ],
      }),
    );
    expect(rows.find((r) => r.key === 'provider:spare')).toMatchObject({
      provider: 'spare',
      slot: NONE,
      health: 'DOWN',
    });
  });

  it('sorts by a column in either direction, ties on the key', () => {
    const rows = mapRows(llm());
    expect(sortMapRows(rows, 'model', false).map((r) => r.key)).toEqual(['queen', 'worker']);
    expect(sortMapRows(rows, 'model', true).map((r) => r.key)).toEqual(['worker', 'queen']);
    const same = rows.map((r) => ({ ...r, effort: 'HIGH' }));
    expect(sortMapRows(same, 'effort', true).map((r) => r.key)).toEqual(['queen', 'worker']);
  });

  it('does not change the rows it was given', () => {
    const rows = mapRows(llm());
    const copy = [...rows];
    sortMapRows(rows, 'provider', true);
    expect(rows).toEqual(copy);
  });
});

describe('spend', () => {
  it('totals each Warden in cents and sorts by Warden id', () => {
    const totals = spendByWarden([
      grant({ id: 'a', holder: 'w_2', spent: 0.1, spend_budget: 1 }),
      grant({ id: 'b', holder: 'w_1', spent: 0.2, spend_budget: 2 }),
      grant({ id: 'c', holder: 'w_2', spent: 0.2, spend_budget: 1 }),
    ]);
    expect(totals).toEqual([
      { holder: 'w_1', spent: 0.2, budget: 2 },
      { holder: 'w_2', spent: 0.3, budget: 2 },
    ]);
  });
});
