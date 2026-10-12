# observation-web/src/views/honey

The Honey browser, `#/honey` (the root) and `#/honey/<path>`: the Hive's knowledge as a folder
tree, read-only, exactly as the Entrance cuts it for this device. Folders are `/hive`,
`/cells/<id>` (with the Cell's live Cell Wax under `/wax`), `/bees/<id>`, `/tasks/<id>` and
`/bee-bread`. A folder shows the viewer's clearance ceiling, the Entrance's note when the listing
is not what one would expect, its sub-folders and a page of items ("More" pages by cursor). Every
item shows its `HoneyClearance` as a tag with words, its kind, its provenance (task, Cell, bee,
source, when) and, for Cell Wax, severity, proposer and expiry. Clicking an item opens it in the
reading pane (beside the folder from 960px, in place of it at phone width): summary, content in a
scrolling pre-formatted box, reason and the Honey row's other sources. A searchable folder
(`can_search`) has a search box; hits show score, snippet, clearance and provenance, with "More
results" and the page's own note.

What shows follows the viewer's scopes: a folder the device may not see is absent from its
parent, opening it by address shows the refusal sentence (never the scope or label), and an item
that is gone says so. There is no Honey stream. A folder is read on navigation and read again, once
per burst a second later, when `/v1/trail/stream` carries a Honey event for it (for a Cell's
folder a `honey.*` or `memory.wax_*` event naming that Cell; for any other folder a `honey.*`
event). The re-read asks for as many entries as the reader has paged in. Without a frame there is
no read.

Nothing writes. "Propose a note" has no path into the House Bee's queue through the Landing Board
yet, so the browser draws no propose control; each folder says that proposing a note is not
available from the Observation Hive yet. The one write the Observation Hive has is the chat.

`slice/` is the view's slice (`state.ts`, the merges and the trail rule in `fold.ts`, the reducer);
`hooks/` holds the three reads (`use_folder.ts` with paging, the trail stream and the throttled
re-read; `use_reading.ts`; `use_search.ts`); `parts/` the small pieces (clearance tag, provenance,
wax facts, item row, breadcrumbs, words and links); `panels/` the folder, search and reading
panels; `honey_screen.tsx` is the route component (`index.ts` exports it as `HoneyScreen`;
`honey.tsx` keeps the old `HoneyView` name for the outlet); `honey.css` the styles.
