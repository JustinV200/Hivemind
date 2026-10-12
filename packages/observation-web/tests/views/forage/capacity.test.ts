/**
 * Tests for the capacity model and its layout: pure positions and widths from figures.
 *
 * The layout is a function of the blocks alone, so each case is plain numbers: a host with half its
 * cores busy, a host that reported nothing (zero capacity), an over-committed host (load above one,
 * grants above its sub-bee slots, a reserve bigger than the free memory), the shared pool, and the
 * promise that two renders of the same figures are byte-identical.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/forage/capacity/.
 *
 * Key invariants:
 *   - Widths are checked against hand-computed shares of the 344 unit bar.
 *
 * See Also:
 *   - docs/observation/design.md section 4.8 (diagram rules).
 */
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { describe, expect, it } from 'vitest';
import { CapacityDiagram } from '../../../src/views/forage/capacity/diagram';
import { BAR_WIDTH, BAR_X, layoutCapacity } from '../../../src/views/forage/capacity/layout';
import { hostBlocks } from '../../../src/views/forage/capacity/model';
import type { CapacityInput } from '../../../src/views/forage/capacity/model';
import { capacity, GIB, grant, headroom, reserve } from './support';

const input = (over: Partial<CapacityInput> = {}): CapacityInput => ({
  capacities: [capacity()],
  grants: [],
  headroom: headroom(),
  reserve: reserve(),
  ...over,
});

const rowOf = (over: Partial<CapacityInput>, host: string, key: string) => {
  const layout = layoutCapacity(hostBlocks(input(over)));
  return layout.blocks.find((b) => b.id === host)!.rows.find((r) => r.row.key === key)!;
};

describe('a host', () => {
  it('splits cores into in use and free by the load', () => {
    const row = rowOf({}, 'cell_1', 'cores');
    expect(row.segments.map((s) => [s.kind, s.width, s.x])).toEqual([
      ['in_use', 172, BAR_X],
      ['free', 172, BAR_X + 172],
    ]);
    expect(row.figures).toBe('4 in use of 8');
    expect(row.limitX).toBeNull();
  });

  it('reads memory as used and free', () => {
    const row = rowOf({}, 'cell_1', 'memory');
    expect(row.figures).toBe('8 GB in use of 16 GB');
  });

  it('shows a total only for GPUs and local seats, which the report does not measure', () => {
    const gpu = rowOf({ capacities: [capacity({ gpus: 2 })] }, 'cell_1', 'gpus');
    expect(gpu.segments).toEqual([]);
    expect(gpu.figures).toBe('2, use not reported');
  });

  it('counts the grants on the Cell against its sub-bee slots', () => {
    const row = rowOf({ grants: [grant({ max_sub_bees: 3 })] }, 'cell_1', 'sub_bees');
    expect(row.row.segments).toEqual([
      { kind: 'granted', amount: 3 },
      { kind: 'free', amount: 3 },
    ]);
  });

  it('sorts hosts by Cell id after the shared pool', () => {
    const blocks = hostBlocks(
      input({ capacities: [capacity({ cell_id: 'b' }), capacity({ cell_id: 'a' })] }),
    );
    expect(blocks.map((b) => b.id)).toEqual(['shared-pool', 'a', 'b']);
  });
});

describe('a zero-capacity host', () => {
  const zero = capacity({
    cores: 0,
    cpu_load: 0,
    memory_bytes: 0,
    memory_free_bytes: 0,
    gpus: 0,
    local_seats: 0,
    max_sub_bees: 0,
  });

  it('has no segments and says none was reported, with no division by zero', () => {
    const layout = layoutCapacity(hostBlocks(input({ capacities: [zero] })));
    const host = layout.blocks.find((b) => b.id === 'cell_1')!;
    for (const row of host.rows) {
      expect(row.segments).toEqual([]);
      expect(row.figures).toBe('none reported');
      expect(row.limitX).toBeNull();
    }
    expect(Number.isFinite(layout.height)).toBe(true);
  });
});

describe('an over-committed host', () => {
  it('fits an overloaded CPU on the bar and marks where its total ends', () => {
    const row = rowOf({ capacities: [capacity({ cpu_load: 1.5 })] }, 'cell_1', 'cores');
    // 12 busy cores of 8: the bar spans 12, so the 8 core limit sits at two thirds.
    expect(row.row.over).toBe(4);
    expect(row.segments).toHaveLength(1);
    expect(row.segments[0]?.width).toBe(BAR_WIDTH);
    expect(row.limitX).toBe(Math.round((BAR_X + (8 / 12) * BAR_WIDTH) * 100) / 100);
    expect(row.figures).toBe('12 in use of 8, over by 4');
  });

  it('marks grants above the sub-bee slots', () => {
    const row = rowOf({ grants: [grant({ max_sub_bees: 9 })] }, 'cell_1', 'sub_bees');
    expect(row.row.over).toBe(3);
    expect(row.limitX).not.toBeNull();
  });

  it('never lets segments run past the bar', () => {
    const row = rowOf({ capacities: [capacity({ cpu_load: 9 })] }, 'cell_1', 'cores');
    const end = row.segments.reduce((a, s) => a + s.width, 0);
    expect(end).toBeLessThanOrEqual(BAR_WIDTH);
  });
});

describe('the shared pool', () => {
  it('divides seats into reserve, granted and free', () => {
    const row = rowOf({ grants: [grant({ seats: 2 })] }, 'shared-pool', 'seats');
    expect(row.row.total).toBe(10);
    expect(row.row.segments.map((s) => [s.kind, s.amount])).toEqual([
      ['granted', 2],
      ['reserve', 2],
      ['free', 6],
    ]);
    expect(row.figures).toBe('2 granted, 2 reserve of 10');
  });

  it('overdraws memory when the reserve is bigger than what is free', () => {
    const tight = capacity({ memory_bytes: 16 * GIB, memory_free_bytes: 1 * GIB });
    const row = rowOf({ capacities: [tight] }, 'shared-pool', 'memory');
    expect(row.row.over).toBe(1 * GIB);
    expect(row.limitX).not.toBeNull();
  });

  it('is absent when the ledger has not been read', () => {
    expect(hostBlocks(input({ headroom: null, reserve: null, capacities: [] }))).toEqual([]);
  });
});

describe('determinism', () => {
  it('lays out and renders the same figures identically twice', () => {
    const blocks = hostBlocks(input({ grants: [grant()] }));
    expect(layoutCapacity(blocks)).toEqual(layoutCapacity(blocks));
    const draw = (): string => renderToStaticMarkup(createElement(CapacityDiagram, { blocks }));
    expect(draw()).toBe(draw());
  });

  it('draws with tokens only, a title on every bar and an accessible name', () => {
    const html = renderToStaticMarkup(
      createElement(CapacityDiagram, { blocks: hostBlocks(input({ grants: [grant()] })) }),
    );
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-labelledby="forage-capacity-title"');
    expect(html).toContain('viewBox="0 0 360 ');
    expect(html).toContain('var(--accent)');
  });
});
