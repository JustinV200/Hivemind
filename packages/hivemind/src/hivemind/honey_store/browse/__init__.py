"""Browse the Honey Store as a read-only folder tree: list, read, search, propose, relabel.

The Honey Store is the Hive's cold-tier knowledge base: Nectar (raw findings) ripened into Honey
(labelled, searchable knowledge). This sub-package is roadmap step 7.10's browser: `HoneyBrowser`
walks it like a filesystem (`/hive`, `/cells/<cell>` with the Cell's live Cell Wax under `wax`,
`/bees/<bee>`, `/tasks/<task>`, `/bee-bread`), filtering every listing and document by the
reader's `honey:read` capabilities and clearance ceiling exactly as retrieval does, and never
writing Honey; a note proposed from a folder becomes a Cell Wax proposal or a note queued for the
House Bee (the maintenance Worker). `HoneyRelabeller` is kept apart from the browser on purpose:
it is the human's own recorded raise or lowering of one row's label, reached by the same path.
Split by responsibility (codingrules 5.2) into the path grammar, the injected sources, listing
entries, per-item visibility and documents, folder listings, notes, relabelling and the browser.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Used
    by `hive honey` (`hivemind.cli.honey`) and, later, the Observation Hive. Calls into the
    honey_store's own store, scope, clearance, identity, models and retrieval, `hivemind.cell`,
    `hivemind.pheromone` and `waggle`; never `hivemind.memory` (wax and Bee Bread arrive through
    the two injected source Protocols).

Key invariants:
    - Listing, reading and searching never write any store; a proposed note writes one queue row
      and its event; only `HoneyRelabeller` changes Honey, and always records the change.
    - Whatever is not in `__all__` is private to this sub-package (codingrules 5.4).

See Also:
    - .claude/roadmap.md steps 7.10 and 7.11 for the browser and the CLI built on it.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for scopes, labels and "browsing never
      writes".
    - hivemind.honey_store.honey for the retriever a folder's search runs.

Public API:
    - HoneyBrowser, operator_reader, HUMAN_READER_MAX_TOKENS, EMPTY_INDEX_REASON (browser): list,
      read, search and propose by path; the operator's own reader.
    - BrowserDeps, BrowseSources, LiveWaxSource, BeeBreadSource, WaxNote, BeeBreadNote
      (sources): what the browser reads through.
    - FakeLiveWaxSource, FakeBeeBreadSource (fake): in-memory sources.
    - BrowsePath, PathKind, parse_path, ROOT_PATH, TOP_FOLDERS (paths): the path grammar.
    - BrowseEntry, BrowseListing, EntryKind (listing): what `ls` returns.
    - BrowseDocument, honey_visible (documents): what `cat` returns, and the row visibility rule.
    - Page, FIRST_PAGE, DEFAULT_PAGE_ROWS, MAX_PAGE_ROWS (folders): listing pages.
    - NoteProposal, CellWaxProposal, QueuedHoneyNote, NOTE_CLEARANCE (notes): a proposed note.
    - HoneyRelabeller, RelabelRequest, RelabelOutcome, RelabelDirection (relabel): the human's
      label change.
    - BrowseError, BrowsePathError, BrowseNotFoundError, BrowseInputError (errors).
"""

from hivemind.honey_store.browse.browser import (
    EMPTY_INDEX_REASON,
    HUMAN_READER_MAX_TOKENS,
    HoneyBrowser,
    operator_reader,
)
from hivemind.honey_store.browse.documents import BrowseDocument, honey_visible
from hivemind.honey_store.browse.errors import (
    BrowseError,
    BrowseInputError,
    BrowseNotFoundError,
    BrowsePathError,
)
from hivemind.honey_store.browse.fake import FakeBeeBreadSource, FakeLiveWaxSource
from hivemind.honey_store.browse.folders import DEFAULT_PAGE_ROWS, FIRST_PAGE, MAX_PAGE_ROWS, Page
from hivemind.honey_store.browse.listing import BrowseEntry, BrowseListing, EntryKind
from hivemind.honey_store.browse.notes import (
    NOTE_CLEARANCE,
    CellWaxProposal,
    NoteProposal,
    QueuedHoneyNote,
)
from hivemind.honey_store.browse.paths import (
    ROOT_PATH,
    TOP_FOLDERS,
    BrowsePath,
    PathKind,
    parse_path,
)
from hivemind.honey_store.browse.relabel import (
    HoneyRelabeller,
    RelabelDirection,
    RelabelOutcome,
    RelabelRequest,
)
from hivemind.honey_store.browse.sources import (
    BeeBreadNote,
    BeeBreadSource,
    BrowserDeps,
    BrowseSources,
    LiveWaxSource,
    WaxNote,
)

__all__ = [
    "DEFAULT_PAGE_ROWS",
    "EMPTY_INDEX_REASON",
    "FIRST_PAGE",
    "HUMAN_READER_MAX_TOKENS",
    "MAX_PAGE_ROWS",
    "NOTE_CLEARANCE",
    "ROOT_PATH",
    "TOP_FOLDERS",
    "BeeBreadNote",
    "BeeBreadSource",
    "BrowseDocument",
    "BrowseEntry",
    "BrowseError",
    "BrowseInputError",
    "BrowseListing",
    "BrowseNotFoundError",
    "BrowsePath",
    "BrowsePathError",
    "BrowseSources",
    "BrowserDeps",
    "CellWaxProposal",
    "EntryKind",
    "FakeBeeBreadSource",
    "FakeLiveWaxSource",
    "HoneyBrowser",
    "HoneyRelabeller",
    "LiveWaxSource",
    "NoteProposal",
    "Page",
    "PathKind",
    "QueuedHoneyNote",
    "RelabelDirection",
    "RelabelOutcome",
    "RelabelRequest",
    "WaxNote",
    "honey_visible",
    "operator_reader",
    "parse_path",
]
