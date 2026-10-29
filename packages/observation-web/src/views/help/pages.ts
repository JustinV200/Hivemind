/**
 * The help pages: which markdown files exist, in what order, and how a page is addressed.
 *
 * Each page is one file under `content/`, bundled as text by Vite's `?raw` import and parsed once
 * here. The order is the order the Help screen lists them: start here, the words, then how the
 * Hive works a theme at a time, then the screens. A page's address is `#/help/<id>`, and a section
 * inside it `#/help/<id>?s=<heading id>`, which is what the screens' "More in Help" links and the
 * legends name.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/`. Read by `help_screen.tsx`, `search.ts` and, for
 *   the links, by `src/components/view_title.tsx` and the Fleet and Honey legends through the
 *   router's `hrefFor`.
 *
 * Key invariants:
 *   - Page ids are stable: a link from another screen names one, and the glossary test reads
 *     `glossary` and `screens` by id.
 *   - The text is parsed once at module load; the blocks are shared, never mutated.
 *
 * See Also:
 *   - src/views/help/content/ for the words themselves.
 */
import { parseMarkdown } from './markdown';
import type { Block } from './markdown';
import bees from './content/bees.md?raw';
import capping from './content/capping.md?raw';
import devices from './content/devices.md?raw';
import forage from './content/forage.md?raw';
import glossary from './content/glossary.md?raw';
import honey from './content/honey.md?raw';
import screens from './content/screens.md?raw';
import start from './content/start.md?raw';

export interface HelpPage {
  id: string;
  title: string;
  // One line under the title in the page list, saying what the page holds.
  summary: string;
  blocks: readonly Block[];
}

const page = (id: string, title: string, summary: string, text: string): HelpPage => ({
  id,
  title,
  summary,
  blocks: parseMarkdown(text),
});

export const HELP_PAGES: readonly HelpPage[] = [
  page('start', 'Start here', 'What this app shows, and how to read it.', start),
  page('glossary', 'Words', 'Every bee term, in plain language.', glossary),
  page(
    'bees',
    'Bees and Cells',
    'The Queen, Wardens, Workers and the machines they work on.',
    bees,
  ),
  page('forage', 'Forage and cost', 'Capacity, grants, model seats and what gets spent.', forage),
  page('honey', 'Memory and Honey', 'Hot state, Bee Bread, Honey, clearances and Cell Wax.', honey),
  page(
    'capping',
    'Capping and guard rails',
    'The change gate, the judge, the Nurse Bee, the Guard Bee and approvals.',
    capping,
  ),
  page(
    'devices',
    'Devices and the Hive Stand',
    'The Entrance, enrolment, the Pollen Packet, the stand key and step-up.',
    devices,
  ),
  page('screens', 'The screens', 'How to read and use each screen.', screens),
];

// The page the bare `#/help` opens.
export const DEFAULT_PAGE_ID = 'start';

export function helpPage(id: string | null): HelpPage | null {
  return HELP_PAGES.find((candidate) => candidate.id === (id ?? DEFAULT_PAGE_ID)) ?? null;
}
