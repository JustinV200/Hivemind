# observation-web/src/views/help

The Help view, `#/help` and `#/help/<page>`: the help pages, a search over them, and the guided
tour. The pages are markdown files under `content/` (start here, the words, bees and Cells,
Forage and cost, memory and Honey, Capping and guard rails, devices and the Hive Stand, the
screens), bundled as text by Vite's `?raw` import and parsed by `markdown.ts`, a small grammar
(`##` and `###` headings, paragraphs, `-` lists, bold, code, links) with no markdown dependency;
`render.tsx` draws the blocks and gives every heading an id, so `#/help/<page>?s=<heading id>`
lands on a section, which is what every screen's "More in Help" link and the Fleet's and Honey's
legends carry. `search.ts` cuts the pages into sections and finds every section whose words hold
the query, headings first; the screen runs it as the person types, since nothing is read from the
Hive. `pages.ts` is the page table and `help_screen.tsx` the route component (`index.ts` exports it
as `HelpScreen`); `help.css` the styles.

`ask/` is the Ask (ADR-0070), the page list's last entry at `#/help/ask`: `ask_panel.tsx` draws
the note that an AI helper answers from these pages and nothing else, the question box, example
questions, and each exchange with its answer (or, in words, why there is none) and the sections it
read as links into the pages, `(cited)` after the ones the answer rests on; `use_ask.ts` makes the
one read, `GET /v1/help/ask?q=`, one question at a time; `ask.css` the styles. The Entrance answers
from the same markdown: `vite.config.ts` copies `content/*.md` into the build as `help/<page>.md`,
which `hive serve` reads, so the Ask and the pages never disagree.

`tour/` is the guided tour: a step table (`steps.ts`) naming a `data-tour` attribute on a real
element (a view's title row carries `view-title-<its screens section>`, so a step about one
screen is never satisfied by another's) and, where the element lives on another screen, the
route to open first; `use_target.ts`
finds the element after the route has drawn, retrying briefly, and reports it absent so the tour
skips the step; `state.ts` keeps where the tour is as a small external store with the finished
mark in `localStorage` (every read and write guarded); `tour.tsx` is the panel (a non-modal
dialog with Back, Next, Skip tour and "Step N of M" in words, the arrow keys and Escape doing the
same) and the ring drawn around the target, and `TourCard` the card on the Help screen that starts
it. The shell mounts `Tour` once, so it survives the route changes it makes. The tour never starts
on its own and never blocks the page.

The glossary test (`tests/views/help/glossary.test.ts`) reads the nav's labels, the strip's words,
the badge words and the clearance names and fails when one has no `##` entry in `glossary.md` or,
for a screen, no section in `screens.md`.
