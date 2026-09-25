"""Provide HoneyBrowser: walk the Honey Store as a read-only folder tree, list, read and search.

The Honey Store is the Hive's cold-tier knowledge base: Nectar (raw findings) ripened into Honey
(labelled, searchable knowledge). `HoneyBrowser` lets the operator's CLI and, later, the
Observation Hive (the operator's dashboard) walk it the way one walks a filesystem (roadmap 7.10):
`ls` lists a folder, `cat` reads one document, `search` runs the retriever within one folder's
scopes, and `propose_note` is the one way to add to it from a folder -- a Cell Wax proposal from a
Cell's folder, a note queued for the House Bee (the maintenance Worker) from anywhere else. Every
listing and document is filtered by the reader's `honey:read` capabilities and clearance ceiling
exactly as retrieval is, and nothing here writes Honey: every write to it goes through the
Queen's process (intake, ripening), never through a browser.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Built by `hive honey` (its `ls`, `cat` and `propose`) over the store
    `hivemind.cli.stores.open_honey_store` opened and memory-backed wax and Bee Bread sources,
    with no retriever (those three need no model); a caller that searches (the Observation Hive,
    later) passes the retriever `build_honey_access` built. Calls into this sub-package's
    `paths`, `folders`, `documents`, `notes` and `sources`, and the honey_store retrieval and
    scope modules.

Key invariants:
    - `ls`, `cat` and `search` never write to any store; `search`'s one write is the retriever's
      own `honey.queried` event, as for every query.
    - A search in an index folder covers every scope `ls` lists there that holds Honey, and a
      search in an index folder with no such scope finds nothing (it never widens to every scope).

See Also:
    - .claude/roadmap.md step 7.10 for the tree this walks.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for "Browsing never writes."
    - hivemind.honey_store.browse.relabel for the human's own label change, kept apart from it.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.documents import BrowseDocument, read_document
from hivemind.honey_store.browse.errors import BrowseError
from hivemind.honey_store.browse.folders import FIRST_PAGE, Page, list_folder, search_scopes
from hivemind.honey_store.browse.listing import BrowseListing
from hivemind.honey_store.browse.notes import NoteProposal, propose_note
from hivemind.honey_store.browse.paths import parse_path
from hivemind.honey_store.browse.sources import BrowserDeps
from hivemind.honey_store.honey import HoneyReader, HoneySearch
from hivemind.honey_store.scope import queen_read_capabilities
from waggle.ids import HiveId
from waggle.messages.honey import HoneyResponse
from waggle.messages.honey.exchange import DEFAULT_MAX_HITS

# A human reads these hits, not a model with a context window: ask past any sane manifest ceiling
# so `[honey.retrieval] max_budget_tokens` (which the retriever always applies) is the one budget.
HUMAN_READER_MAX_TOKENS = 1_000_000
EMPTY_INDEX_REASON = "No folder here holds Honey this reader may see, so nothing was searched."

__all__ = [
    "EMPTY_INDEX_REASON",
    "HUMAN_READER_MAX_TOKENS",
    "HoneyBrowser",
    "operator_reader",
]


class HoneyBrowser:
    """Read the Honey Store by path for one reader at a time; holds no state beyond its deps."""

    def __init__(self, deps: BrowserDeps) -> None:
        """Keep the store, retriever, sources, identity and clock every call shares.

        Args:
            deps: What the browser reads through; see BrowserDeps.
        """
        self._deps = deps

    async def ls(self, path: str, reader: HoneyReader, page: Page = FIRST_PAGE) -> BrowseListing:
        """List the folder at `path` as `reader` may see it.

        Args:
            path: A browser path; a document path lists as that one document.
            reader: Its `honey:read` capabilities and clearance ceiling.
            page: Which page to list; the first `DEFAULT_PAGE_ROWS` entries by default.

        Returns:
            One page of the folder's visible entries, each with its clearance and provenance.

        Raises:
            BrowsePathError: `path` names no place in the tree.
            BrowseNotFoundError: `path` is a document with nothing visible at it.
        """
        return await list_folder(self._deps, parse_path(path), reader, page)

    async def cat(self, path: str, reader: HoneyReader) -> BrowseDocument:
        """Read the one document at `path`, if `reader` may see it.

        Args:
            path: A document path: a Honey row, a live wax note or a Bee Bread entry.
            reader: Its `honey:read` capabilities and clearance ceiling.

        Returns:
            The Honey row (title, summary, body, clearance, scope, kind, origin, provenance and
            embedding model), the wax note, or the Bee Bread entry.

        Raises:
            BrowsePathError: `path` does not parse, or names a folder.
            BrowseNotFoundError: Nothing at `path` is visible to `reader`.
        """
        return await read_document(self._deps, parse_path(path), reader)

    async def search(
        self, path: str, text: str, reader: HoneyReader, max_hits: int = DEFAULT_MAX_HITS
    ) -> HoneyResponse:
        """Search the Honey under the folder at `path`, exactly as a query by `reader` would.

        Args:
            path: A folder holding Honey: the root, an index folder or one scope's folder.
            text: The query's words; never recorded anywhere.
            reader: Its `honey:read` capabilities and clearance ceiling.
            max_hits: The most hits to return; 1 to `waggle`'s MAX_MAX_HITS.

        Returns:
            The retriever's response, limited to the folder's scopes; an empty response with
            EMPTY_INDEX_REASON for an index folder listing nothing.

        Raises:
            BrowseError: This browser was built without a retriever.
            BrowsePathError: `path` does not parse, or holds no Honey (a document, a Cell's live
                wax, Bee Bread).
            HoneyStoreError: The store itself failed.
        """
        retriever = self._deps.retriever
        # A browser built only to list and read has no retriever: say so rather than guess.
        if retriever is None:
            raise BrowseError(
                "This Honey browser was built without a retriever, so it cannot search; "
                "`hive honey query` searches with the Hive's own retriever."
            )
        scopes = await search_scopes(self._deps, parse_path(path), reader)
        # An index folder with no visible scope: searching "every scope" instead would widen it.
        if scopes is None:
            return HoneyResponse(
                hits=(),
                token_count=0,
                is_truncated=False,
                filtered_count=0,
                reason=EMPTY_INDEX_REASON,
            )
        search = HoneySearch(
            text=text,
            reader=reader,
            requested_scopes=scopes,
            max_hits=max_hits,
            max_tokens=HUMAN_READER_MAX_TOKENS,
        )
        # The retriever's own filtered, ranked search; its one model call has its own timeout.
        return await retriever.search(search)

    async def propose_note(self, path: str, title: str, text: str) -> NoteProposal:
        """Propose a note from the folder at `path`: Cell Wax from a Cell's folder, else queued.

        Args:
            path: Where the note is proposed; a document path counts as its folder.
            title: A one-line label.
            text: The note itself.

        Returns:
            A CellWaxProposal for the caller to file through the memory tier's wax proposal
            path, or the QueuedHoneyNote already waiting for the House Bee.

        Raises:
            BrowsePathError: `path` does not parse, or names a Cell whose id is not a Cell id.
            BrowseInputError: The title or text is empty or too long.
        """
        return await propose_note(self._deps, path, title, text)


def operator_reader(hive_id: HiveId, ceiling: HoneyClearance) -> HoneyReader:
    """Build the reader the operator browses and queries as: every scope, up to `ceiling`.

    ADR-0035's defaults until phase 10 issues real capability sets: the Queen, the House Bee and
    the operator's CLI read every scope. The operator reads from the Hive Stand, never from a
    Night Veil Cell, so its queries are trailed like any other.

    Args:
        hive_id: The Hive's own id, the requester every `honey.queried` event names.
        ceiling: The most sensitive label the operator asked to see.

    Returns:
        A HoneyReader holding `honey:read:*` with `ceiling` as its clearance ceiling.
    """
    return HoneyReader(
        requester=hive_id,
        capabilities=queen_read_capabilities(),
        ceiling=ceiling,
        is_night_veil=False,
    )
