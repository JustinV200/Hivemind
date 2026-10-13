"""Hold the Honey browser's read models: folders, items, search hits, queries and their cursor.

Roadmap step 12.9: the Observation Hive's Honey browser shows the Hive's knowledge as a folder
tree over provenance (codingrules 8.11). Split by responsibility (codingrules 5.2) into the item
models (what one item is, where it came from, its text), the folder models, the search models and
the query strings with the opaque cursor their pages continue from. Each model that is built from
a record the Honey browser returns has a pure constructor beside it (``of_entry``,
``of_document``, ``of_listing``, ``of_hit``, ``of_hits``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views``. Re-exported
    by ``hivemind.observation.views`` and ``hivemind.observation``; used by
    ``hivemind.entrance.reads.honey`` and ``hivemind.entrance.routes.hive.honey``. Calls into the
    Honey browser's types and pydantic.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A view holds only what the browser let the viewer see; nothing here widens it.

See Also:
    - hivemind.honey_store.browse for the tree and its visibility rules.

Public API:
    - HoneyItemKind, HoneyProvenanceView, HoneyItemSummary, HoneyItemView (items).
    - HoneyFolderEntryView, HoneyFolderView (folders).
    - HoneySearchHit, HoneySearchPage (search).
    - HoneyTreeQuery, HoneySearchQuery, encode_honey_cursor, decode_honey_cursor,
      DEFAULT_HONEY_PAGE, MAX_HONEY_PAGE, DEFAULT_SEARCH_PAGE, MAX_SEARCH_PAGE (queries).
"""

from hivemind.observation.views.honey.folders import HoneyFolderEntryView, HoneyFolderView
from hivemind.observation.views.honey.items import (
    HoneyItemKind,
    HoneyItemSummary,
    HoneyItemView,
    HoneyProvenanceView,
)
from hivemind.observation.views.honey.queries import (
    DEFAULT_HONEY_PAGE,
    DEFAULT_SEARCH_PAGE,
    MAX_HONEY_PAGE,
    MAX_SEARCH_PAGE,
    HoneySearchQuery,
    HoneyTreeQuery,
    decode_honey_cursor,
    encode_honey_cursor,
)
from hivemind.observation.views.honey.search import HoneySearchHit, HoneySearchPage

__all__ = [
    "DEFAULT_HONEY_PAGE",
    "DEFAULT_SEARCH_PAGE",
    "MAX_HONEY_PAGE",
    "MAX_SEARCH_PAGE",
    "HoneyFolderEntryView",
    "HoneyFolderView",
    "HoneyItemKind",
    "HoneyItemSummary",
    "HoneyItemView",
    "HoneyProvenanceView",
    "HoneySearchHit",
    "HoneySearchPage",
    "HoneySearchQuery",
    "HoneyTreeQuery",
    "decode_honey_cursor",
    "encode_honey_cursor",
]
