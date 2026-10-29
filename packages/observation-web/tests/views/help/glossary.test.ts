/**
 * The glossary test: every bee word the shell shows has an entry, and every screen a section.
 *
 * The nav's labels, the strip's words, the badge words and the clearance names are the words a
 * newcomer meets before opening anything, so each must be a `##` entry in the Help's glossary;
 * and every nav item must have a section in the screens page, which is where its "More in Help"
 * link lands. The list of shell words is written out by hand here, beside the nav's own table, so
 * a new label fails this test until it is explained.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Reads src/views/help/content/ through the page table.
 *
 * Key invariants:
 *   - A label matches an entry by its words, singular or plural (`Bees` finds `Bee`), and an entry
 *     may carry a code after its words (`Royal (C2)`).
 *
 * See Also:
 *   - src/views/help/content/glossary.md and screens.md.
 */
import { describe, expect, it } from 'vitest';
import { CLEARANCE_NAMES } from '../../../src/components';
import { NAV_ITEMS } from '../../../src/shell/nav_items';
import { helpPage } from '../../../src/views/help';

// The level-2 headings of a page, lower-cased, with any trailing code dropped (`royal (c2)`).
function headings(id: string): string[] {
  return (helpPage(id)?.blocks ?? []).flatMap((block) =>
    block.kind === 'heading' && block.level === 2
      ? [block.text.toLowerCase().replace(/\s*\(.*\)$/, '')]
      : [],
  );
}

const GLOSSARY = headings('glossary');
const SCREENS = headings('screens');

// The words the shell shows before any view opens: the nav, the strip, the badges, the clearances.
const SHELL_WORDS = [
  ...NAV_ITEMS.map((item) => item.label),
  'Bees',
  'Cells',
  'Thinking',
  'Meadow',
  'Propolis',
  'Night Veil',
  'Pheromone Mask',
  ...Object.values(CLEARANCE_NAMES),
];

describe('glossary', () => {
  it.each(SHELL_WORDS)('has an entry for %s', (word) => {
    const lower = word.toLowerCase();
    expect(GLOSSARY.includes(lower) || GLOSSARY.includes(lower.replace(/s$/, ''))).toBe(true);
  });

  it('has a screens section for every nav item', () => {
    for (const item of NAV_ITEMS) {
      expect(SCREENS, item.label).toContain(item.label.toLowerCase());
    }
  });
});
