"""Define the Honey browser's search models: ranked hits and a page of them.

``HoneySearchPage`` is one page of a folder's search: the retriever's ranked hits, each with its
score, snippet, scope, ``HoneyClearance`` label and provenance, and the cursor for the next page.
The retriever has already limited the hits to the viewer's scopes and clearance; its own account
of how many it withheld is never shown, since that count would tell a viewer how much it may not
read. A hit's id opens the item with ``/v1/honey/items/{id}``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views.honey``.
    Built by ``hivemind.entrance.reads.honey``; published in the OpenAPI document. Calls into
    waggle's hit, this package's item models and cursor, and pydantic.

Key invariants:
    - Pure: hits are handed in and a page comes out.
    - A page is a slice of the ranked hits; a cursor exists only when a hit lies beyond it.

See Also:
    - hivemind.honey_store.browse.browser for the search these pages come from.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.observation.views.honey.items import (
    HoneyProvenanceView,
    last_segment,
    provenance_view,
)
from hivemind.observation.views.honey.queries import encode_honey_cursor
from waggle.messages.honey import HoneyHit

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = ["HoneySearchHit", "HoneySearchPage"]


class HoneySearchHit(BaseModel):
    """One ranked search result (observe:honey:<scope>)."""

    model_config = _CONFIG

    id: str = Field(description="The hit's item id; open it with /v1/honey/items/{id}.")
    path: str = Field(description="The hit's browser path.")
    title: str = Field(description="The row's one-line title.")
    snippet: str = Field(description="The passage that matched, bounded.")
    score: float = Field(description="Relevance, 0 (no match) to 1 (perfect).")
    scope: str = Field(description="The scope it was found in.")
    clearance: HoneyClearance = Field(description="The row's data-sensitivity label.")
    provenance: HoneyProvenanceView = Field(description="Task, Cell, bee and when.")

    @classmethod
    def of_hit(cls, hit: HoneyHit) -> HoneySearchHit:
        """Shape one retrieval hit; its reference is the row's browser path.

        Args:
            hit: A hit the retriever returned within the viewer's scopes and clearance.

        Returns:
            The hit as the view shows it.
        """
        return cls(
            id=last_segment(hit.honey_ref),
            path=hit.honey_ref,
            title=hit.title,
            snippet=hit.excerpt,
            score=hit.score,
            scope=hit.scope,
            clearance=HoneyClearance.from_wire(hit.clearance),
            provenance=provenance_view(hit.provenance, None),
        )


class HoneySearchPage(BaseModel):
    """One page of a folder's search, ranked."""

    model_config = _CONFIG

    path: str = Field(description="The folder searched.")
    hits: list[HoneySearchHit] = Field(description="The page's hits, best first.")
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at end.")
    note: str = Field(description="Why the result is what it is, when not obvious; else empty.")

    @classmethod
    def of_hits(
        cls, path: str, hits: tuple[HoneyHit, ...], offset: int, limit: int, note: str
    ) -> HoneySearchPage:
        """Shape one page of a search's ranked hits.

        Args:
            path: The folder searched, normalised.
            hits: The retriever's ranked hits, from the best: at most one past this page.
            offset: How many hits the earlier pages showed.
            limit: How many hits the page asked for.
            note: Why the result is what it is, when not obvious; else empty.

        Returns:
            The page, and the cursor for the next one when a hit lies beyond it.
        """
        end = offset + limit
        return cls(
            path=path,
            hits=[HoneySearchHit.of_hit(hit) for hit in hits[offset:end]],
            next_cursor=encode_honey_cursor(end) if len(hits) > end else None,
            note=note,
        )
