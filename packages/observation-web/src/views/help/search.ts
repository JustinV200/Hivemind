/**
 * Search over the help pages: every section whose words hold the query, headings first.
 *
 * A section is a `##` heading and the blocks under it (a `###` heading stays inside its section);
 * the blocks before a page's first heading are its opening section, named after the page. A query
 * is split into words, and a section matches when every word is somewhere in its heading or text,
 * ignoring case, so `night veil` finds the glossary entry and `veil tor` the paragraph that says
 * why. Hits whose heading holds the whole query come first (a glossary term beats a passing
 * mention), then the pages' own order. The search runs in the browser over bundled text: no call,
 * no cost, so the screen may run it as the person types.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/`. Read by `help_screen.tsx`.
 *
 * Key invariants:
 *   - Pure: the sections are built once from the pages and never change.
 *   - An empty or all-space query matches nothing, so the screen shows the page instead.
 *
 * See Also:
 *   - src/views/help/pages.ts and markdown.ts.
 */
import { plainText } from './markdown';
import type { Block } from './markdown';
import { HELP_PAGES } from './pages';
import type { HelpPage } from './pages';

export interface HelpSection {
  page: HelpPage;
  // The heading's id (the page id for the opening section) and its words.
  id: string;
  heading: string;
  blocks: readonly Block[];
  // Heading and text in lower case, what a query is looked for in.
  words: string;
}

// The words of a block, for matching: a heading's text, a paragraph's or list's spans.
function wordsOf(block: Block): string {
  if (block.kind === 'heading') return block.text;
  if (block.kind === 'list') return block.items.map(plainText).join(' ');
  return plainText(block.spans);
}

function sectionsOfPage(page: HelpPage): HelpSection[] {
  const sections: HelpSection[] = [];
  let open: HelpSection | null = null;
  const finish = (): void => {
    if (open !== null) {
      open.words = open.blocks.map(wordsOf).join(' ').toLowerCase();
      sections.push(open);
    }
  };
  for (const block of page.blocks) {
    // A `##` heading starts a new section; the opening section starts on the first block.
    if (block.kind === 'heading' && block.level === 2) {
      finish();
      open = { page, id: block.id, heading: block.text, blocks: [block], words: '' };
    } else if (open === null) {
      open = { page, id: page.id, heading: page.title, blocks: [block], words: '' };
    } else {
      open.blocks = [...open.blocks, block];
    }
  }
  finish();
  return sections;
}

export const HELP_SECTIONS: readonly HelpSection[] = HELP_PAGES.flatMap(sectionsOfPage);

export function searchHelp(query: string): HelpSection[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const whole = words.join(' ');
  const hits = HELP_SECTIONS.filter((section) =>
    words.every((word) => section.words.includes(word)),
  );
  // A heading that says the query is the entry the reader meant; the rest keep the pages' order.
  const byHeading = (section: HelpSection): number =>
    section.heading.toLowerCase().includes(whole) ? 0 : 1;
  return hits.sort((a, b) => byHeading(a) - byHeading(b));
}
