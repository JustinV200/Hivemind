/**
 * Tests for the Fleet row's layout: a real table row from 1080 px, a card below.
 *
 * The exit run showed the row as a stacked card under a table header at 1280 px. The markup is the
 * same at every width (a `tr` of `th` and `td` cells in a `table`), so the width decides only the
 * stylesheet: these tests mount the real view, then resolve each part's winning CSS at 1280 and
 * 360 px (`tests/support/cascade.ts`) to prove the row is a table row on the desktop and a flex
 * card on a phone, with the tier and mask badges leading the card.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/fleet/fleet.css and the table markup.
 *
 * Key invariants:
 *   - The cells are `td` elements inside the table's `tbody` at every width.
 *   - The card order is set only below 1080 px; the table has no `order`.
 *
 * See Also:
 *   - tests/styles/table_layout.test.ts for the same check on every view's table.
 */
import { screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { readCss, rulesAt, winning } from '../../support/cascade';
import { fleetRow, openFleet } from './support';

beforeEach(() => window.localStorage.clear());

async function mounted() {
  await openFleet({
    rows: [fleetRow({ id: 'cell_1', comb_shield: 'PROPOLIS', mask_state: 'WARDEN' })],
  });
  const row = (await screen.findAllByRole('row'))[1]!;
  return { row, css: await readCss('src/views/fleet/fleet.css') };
}

describe('fleet row layout', () => {
  it('is a table row of td cells in the table body, at every width', async () => {
    const { row } = await mounted();
    const table = screen.getByRole('table');
    expect(row.parentElement?.tagName).toBe('TBODY');
    expect(row.closest('table')).toBe(table);
    expect(row.querySelector('th[scope="row"]')).not.toBeNull();
    const cells = [...row.children].filter((cell) => cell.tagName === 'TD');
    expect(cells.length).toBeGreaterThan(8);
    expect(row.querySelector('td.fleet-tier .badge-tier')).not.toBeNull();
    expect(row.querySelector('td.fleet-mask .badge-mask')).not.toBeNull();
  });

  it('is laid out as a table row and cells at 1280 px, like its header', async () => {
    const { row, css } = await mounted();
    const rules = rulesAt(css, 1280);
    const header = screen.getAllByRole('row')[0]!;
    expect(winning(header, rules, 'display')).toBe('table-row');
    expect(winning(row, rules, 'display')).toBe('table-row');
    expect(winning(row.parentElement!, rules, 'display')).toBe('table-row-group');
    expect(winning(row.querySelector('td')!, rules, 'display')).toBe('table-cell');
    expect(winning(row.querySelector('th')!, rules, 'display')).toBe('table-cell');
    expect(winning(row.querySelector('td.fleet-links')!, rules, 'display')).toBe('table-cell');
  });

  it('is a card at 360 px with the tier and mask badges first', async () => {
    const { row, css } = await mounted();
    const rules = rulesAt(css, 360);
    expect(winning(row, rules, 'display')).toBe('flex');
    expect(winning(row.querySelector('td')!, rules, 'display')).toBe('block');
    const order = (selector: string) =>
      Number(winning(row.querySelector(selector)!, rules, 'order'));
    expect(order('td.fleet-tier')).toBeLessThan(order('td.fleet-mask'));
    expect(order('td.fleet-mask')).toBeLessThan(order('th.fleet-id'));
    expect(order('th.fleet-id')).toBeLessThan(0);
    // At 1280 px the order is gone, so the columns keep their header's sequence.
    expect(winning(row.querySelector('td.fleet-tier')!, rulesAt(css, 1280), 'order')).toBe('0');
  });
});
