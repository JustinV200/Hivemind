# hivemind.honey_store.browse

The Honey Store's read-only folder tree (roadmap step 7.10): walk Honey (ripened, labelled,
retrievable knowledge) the way one walks a filesystem, so the operator's CLI (`hive honey ls`,
`cat`, `propose`, `relabel`) and, later, the Observation Hive see the Hive's knowledge by place.

```
/                          the five folders below
/hive                      shared knowledge: the `hive` scope's rows
/cells                     one folder per Cell with visible Honey or visible live wax
/cells/<cell>              that Cell's history (`cell:<id>` rows), and its `wax` folder
/cells/<cell>/wax          the Cell's live Cell Wax (WRITTEN, unexpired), from memory
/bees, /bees/<bee>         one bee's own material (`bee:<id>` rows)
/tasks, /tasks/<task>      one task's working material (`task:<id>` rows)
/bee-bread                 recent Bee Bread (the last week), from memory
<folder>/<id>              one document: a Honey row, a wax note, a Bee Bread entry
```

## Modules

- `paths.py` (pure) -- `parse_path(raw) -> BrowsePath` (`PathKind`, the scope, the item id; a
  missing leading `/`, a doubled or trailing `/` are forgiven, `.`/`..` and anything deeper than
  the tree are `BrowsePathError`). Folder scopes come from `scope.scope_for_folder`, so a folder and
  the scope it shows never disagree; `wax_folder_path`, `wax_note_path`, `bee_bread_entry_path`,
  `index_path` build the other paths.
- `sources.py` -- `BrowserDeps` (store, sources, identity, clock, optional retriever) and the two
  memory-side Protocols the browser reads through, since it may not import `hivemind.memory`:
  `LiveWaxSource.live_wax(cell_id | None, allowance)` (WRITTEN notes; None reads every Cell's)
  and `BeeBreadSource.recent(since, until, allowance, limit)` / `.entry(id, allowance)`; their
  neutral shapes `WaxNote` and `BeeBreadNote`. `hivemind.cli.honey.sources` implements both over
  the memory store.
- `fake.py` -- `FakeLiveWaxSource` and `FakeBeeBreadSource`: the same Protocols over fixed lists.
- `listing.py` -- `BrowseEntry` (path, kind, one-line title, detail, clearance, provenance: the
  wire's `HoneyProvenance`), `BrowseListing` (a page, `is_truncated`, a `note`), and one builder
  per item kind.
- `documents.py` -- `read_document` (`cat`) and the visibility rules every read applies:
  `honey_visible` (live, readable scope, label within the ceiling: retrieval's own filter per
  row), `wax_visible` (unexpired too), `bee_bread_visible` over `bee_bread_scope` (`task:<id>` or
  `hive`, the scope its ripened Honey would carry), `visible_honey` and `live_wax_notes`. Anything
  hidden is reported exactly as a missing item (`BrowseNotFoundError`).
- `folders.py` -- `list_folder` (`ls`) for every folder kind, one `Page` at a time (default 50,
  at most 500), and `search_scopes`. The store has no "distinct scopes" read, so `/cells`,
  `/bees` and `/tasks` are derived from `list_honey` pages under the reader's own filter,
  bounded by `MAX_SCAN_ROWS` (the listing says when it stopped); `/cells` also lists Cells whose
  only visible content is live wax.
- `notes.py` -- `propose_note`: from a Cell's folder a `CellWaxProposal` the caller files through
  `hivemind.memory.propose_wax` (nothing written here); anywhere else a `QueuedHoneyNote`, queued
  with `HoneyStore.add_proposal` and a `honey.note_proposed` event (scope and lengths only) for the
  House Bee to take in as HUMAN Nectar, C2 by construction.
- `relabel.py` -- `HoneyRelabeller.relabel(RelabelRequest, reader)`: the human's raise
  (`honey.label_raised`) or, after `clearance.check_lowering` with approver HUMAN, lowering
  (`honey.label_lowered`) of one visible row's label, each event carrying both labels, the
  approver and the reason. Kept apart from `HoneyBrowser`, which never writes Honey.
- `browser.py` -- `HoneyBrowser(deps)`: `ls`, `cat`, `search` (the retriever over the folder's
  scopes: `/` every readable scope, a scope folder its own, an index folder the scopes it lists
  with Honey, nothing at all for an index with none), `propose_note`; and `operator_reader`
  (`honey:read:*` up to a ceiling, the operator's defaults per ADR-0031).
- `errors.py` -- `BrowseError` (a `HoneyStoreError`), `BrowsePathError`, `BrowseNotFoundError`,
  `BrowseInputError`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/honey_store/browse
```

Every test runs over a real SQLite Honey Store with rows ripened as the Ripener stores them and
the shipped fake sources (`tests/unit/honey_store/browse/harness.py`): every path kind, filtering
by capabilities, ceiling, taint, retirement and expiry, paging and the scan bound, a search
limited to a folder's scopes, propose both ways, and relabel both ways with its events.
