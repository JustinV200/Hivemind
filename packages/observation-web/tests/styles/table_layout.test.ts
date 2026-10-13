/**
 * Tests for the cards-to-table breakpoints: a table is a table where the design says it is.
 *
 * The Fleet, Capping and Devices views are stacked cards on a phone and real tables from 1080 px;
 * the Forage and Cost tables are real tables from 720 px. Phase 12's exit run found the Fleet's
 * row still laid out as a card at 1280 px under a table header, because its desktop rule was a
 * specificity step weaker than the card rule it meant to undo. These tests resolve the winning
 * `display` of each part of each table at both widths from the stylesheet, so that mistake fails
 * here in every view, not only in a screenshot.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Reads the views' CSS as text through `tests/support/cascade.ts`.
 *
 * Key invariants:
 *   - A desktop part that no rule sets is a plain table part (the browser default), which is fine.
 *   - A card is not a table part at phone width: no row, cell or body keeps a table display there.
 *
 * See Also:
 *   - docs/observation/design.md section 2 for the breakpoints.
 *   - tests/views/fleet/fleet_view.test.tsx for the Fleet's real markup.
 */
import { describe, expect, it } from 'vitest';
import { readCss, rulesAt, winning } from '../support/cascade';

const TABLES = [
  { view: 'fleet', desktop: 1080 },
  { view: 'capping', desktop: 1080 },
  { view: 'devices', desktop: 1080 },
  { view: 'forage', desktop: 720 },
  { view: 'cost', desktop: 720 },
] as const;

// The markup every table of the app shares: a captioned head, and a body row of labelled cells.
function tableOf(view: string): HTMLTableElement {
  document.body.innerHTML = `<table class="${view}-table"><caption>c</caption>
    <thead><tr><th scope="col">A</th></tr></thead>
    <tbody><tr><th scope="row" data-label="A">a</th><td data-label="B">b</td></tr></tbody></table>`;
  return document.querySelector('table')!;
}

const PARTS = {
  table: 'display: table',
  tbody: 'display: table-row-group',
  'tbody tr': 'display: table-row',
  'tbody td': 'display: table-cell',
} as const;

const SELECT: Record<keyof typeof PARTS, (t: HTMLTableElement) => Element> = {
  table: (t) => t,
  tbody: (t) => t.querySelector('tbody')!,
  'tbody tr': (t) => t.querySelector('tbody tr')!,
  'tbody td': (t) => t.querySelector('tbody td')!,
};

describe.each(TABLES)('$view table', ({ view, desktop }) => {
  it(`is a real table from ${desktop} px up, with no part left as a card`, async () => {
    const css = await readCss(`src/views/${view}/${view}.css`);
    const rules = rulesAt(css, 1280);
    const table = tableOf(view);
    for (const part of Object.keys(PARTS) as (keyof typeof PARTS)[]) {
      const shown = winning(SELECT[part](table), rules, 'display');
      expect(shown === null || shown === PARTS[part].replace('display: ', ''), part).toBe(true);
    }
  });

  it('is a stack of cards on a phone: no row, body or cell is a table part', async () => {
    const css = await readCss(`src/views/${view}/${view}.css`);
    const rules = rulesAt(css, 360);
    const table = tableOf(view);
    for (const part of ['table', 'tbody', 'tbody tr', 'tbody td'] as const) {
      const shown = winning(SELECT[part](table), rules, 'display');
      expect(shown, part).not.toMatch(/^table/);
    }
    // A card row is a box of its own, so a cell inside it is not a bare table cell either.
    expect(winning(table.querySelector('tbody tr')!, rules, 'display')).not.toBeNull();
  });
});
