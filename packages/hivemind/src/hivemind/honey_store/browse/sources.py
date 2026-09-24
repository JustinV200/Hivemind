"""Define what the Honey browser reads through: its deps, and the two memory-side sources.

The Honey browser presents the Honey Store (the Hive's cold-tier knowledge base) as a read-only
folder tree; `BrowserDeps` bundles what it reads through (the store, the retriever, the identity
and clock a proposed note is recorded with) so every browser module takes one value, not five.
Two of its folders show things that are not Honey rows at all: a Cell's `wax` folder shows its
live Cell Wax (the Queen's standing, WRITTEN cautions about that Cell: a known limit, a risk, a
quirk), and `/bee-bread` shows recent Bee Bread (the warm memory tier: indexed episodes,
transcripts, tool results and Handoffs). Both live in `hivemind.memory`, a Layer-2 sibling this
package may not import, so the browser declares what it needs as two Protocols
(`LiveWaxSource`, `BeeBreadSource`) and the composition root (`hive honey`, later the Observation
Hive) implements them over the memory store; `WaxNote` and `BeeBreadNote` are the neutral shapes
they hand back, and the documents `cat` returns for those two folders.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. The Protocols are implemented by `hivemind.cli.honey.sources` over the
    memory store and by `hivemind.honey_store.browse.fake` for tests; everything here is read by
    `hivemind.honey_store.browse.folders`, `.documents`, `.notes` and `.browser`. Calls into
    `hivemind.cell`, this package's own store, retriever and identity, and `waggle` only.

Key invariants:
    - A source never returns an item above the `allowance` it was given; the browser checks the
      label again anyway, so a faulty source can widen nothing.
    - Both note models are frozen and forbid extra fields (codingrules 8.5): they cross from the
      memory adapter to whoever renders a document.

See Also:
    - hivemind.memory.cell_wax and hivemind.memory.bee_bread for the records these mirror.
    - hivemind.honey_store.browse.fake for the in-memory implementations tests use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.honey_store.honey import HoneyRetriever
from hivemind.honey_store.identity import HoneyIdentity
from hivemind.honey_store.store import HoneyStore
from waggle.clock import Clock
from waggle.ids import CellId
from waggle.messages.base import CellIdField, TaskIdField, UtcDatetime
from waggle.messages.cell.wax import WAX_ID_PATTERN, WaxOrigin, WaxSeverity

MAX_KIND_CHARS = 32  # A Bee Bread entry kind's own name (TRANSCRIPT, TOOL_RESULT, ...).

# codingrules 8.5: frozen, extra-forbidding config both note models share.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# A Cell Wax note's id: a plain string until an IdKind exists for it (waggle.messages.cell.wax).
_WaxId = Annotated[str, Field(pattern=WAX_ID_PATTERN)]

__all__ = [
    "MAX_KIND_CHARS",
    "BeeBreadNote",
    "BeeBreadSource",
    "BrowseSources",
    "BrowserDeps",
    "LiveWaxSource",
    "WaxNote",
]


class WaxNote(BaseModel):
    """One live Cell Wax note as the browser shows it: a Queen-written caution about one Cell."""

    model_config = _MODEL_CONFIG

    id: _WaxId = Field(description="The note's own id (a wax_-prefixed ULID).")
    cell_id: CellIdField = Field(description="The Cell the note is about.")
    severity: WaxSeverity = Field(description="NOTE, CAUTION or BLOCK: its weight in placement.")
    text: str = Field(description="The caution itself.")
    reason: str = Field(description="Why its proposer believed it.")
    origin: WaxOrigin = Field(description="Who noticed it: a bee, a Patrol or the human.")
    proposer: str | None = Field(description="The proposing bee's id; None for the human.")
    clearance: HoneyClearance = Field(description="The note's own data-sensitivity label.")
    task_id: TaskIdField | None = Field(description="The task during which it was noticed.")
    proposed_at: UtcDatetime = Field(description="When it was proposed.")
    expires_at: UtcDatetime | None = Field(
        description="When it expires on its own; None for standing wax."
    )


class BeeBreadNote(BaseModel):
    """One Bee Bread entry as the browser shows it: an index row, or a deposited payload in full."""

    model_config = _MODEL_CONFIG

    id: str = Field(description="The entry's own id.")
    kind: str = Field(
        max_length=MAX_KIND_CHARS,
        description="What it indexes or holds: TASK_HISTORY, TRAIL_EVENT, HANDOFF, NOTE, "
        "TRANSCRIPT, TOOL_RESULT or SUMMARY (hivemind.memory.BeeBreadEntryKind's values).",
    )
    task_id: TaskIdField | None = Field(description="The task it concerns, if any.")
    clearance: HoneyClearance = Field(description="The entry's own data-sensitivity label.")
    created_at: UtcDatetime = Field(description="When it was written.")
    text: str | None = Field(description="A short preview, for an index-only entry.")
    payload: str | None = Field(description="The full content, for a payload entry.")
    ref_ids: tuple[str, ...] = Field(description="Ids of the records it indexes, if any.")


class LiveWaxSource(Protocol):
    """Read one Cell's live Cell Wax: the notes the Queen has WRITTEN and not yet cleared."""

    async def live_wax(
        self, cell_id: CellId | None, allowance: HoneyClearance
    ) -> tuple[WaxNote, ...]:
        """Return every WRITTEN Cell Wax note about `cell_id`, within `allowance`, newest first.

        A note past its own `expires_at` may still be here until the House Bee's sweep expires
        it; the browser drops those itself, against its own clock.

        Args:
            cell_id: The Cell whose wax to read; None reads every Cell's, so `/cells` can list a
                Cell whose only content so far is its live wax.
            allowance: The reader's clearance ceiling; no note above it may come back.

        Returns:
            The WRITTEN notes at or below `allowance`, newest first.
        """
        ...


class BeeBreadSource(Protocol):
    """Read Bee Bread, the warm memory tier, by time and by id; lookup only, never search."""

    async def recent(
        self, since: datetime, until: datetime, allowance: HoneyClearance, limit: int
    ) -> tuple[BeeBreadNote, ...]:
        """Return the newest entries written in `[since, until]`, within `allowance`.

        Args:
            since: Inclusive lower bound on `created_at`.
            until: Inclusive upper bound on `created_at`.
            allowance: The reader's clearance ceiling; no entry above it may come back.
            limit: The most entries to return; the newest are kept.

        Returns:
            At most `limit` entries at or below `allowance`, newest first.
        """
        ...

    async def entry(self, entry_id: str, allowance: HoneyClearance) -> BeeBreadNote | None:
        """Return one entry by id, within `allowance`.

        Args:
            entry_id: The entry's own id.
            allowance: The reader's clearance ceiling.

        Returns:
            The entry, or None when no entry has this id or its label is above `allowance`
            (the two are indistinguishable on purpose).
        """
        ...


@dataclass(frozen=True, slots=True)
class BrowseSources:
    """The two memory-side sources the browser reads its non-Honey folders through."""

    wax: LiveWaxSource  # A Cell's live Cell Wax, for `/cells/<id>/wax`.
    bee_bread: BeeBreadSource  # Recent Bee Bread, for `/bee-bread`.


@dataclass(frozen=True, slots=True)
class BrowserDeps:
    """Everything a HoneyBrowser reads through, and what a proposed note is recorded with.

    Attributes:
        store: The Honey Store every listing and document comes from; the browser only reads it,
            except `add_proposal` for a proposed note (a queue row, never a Honey row).
        sources: Live Cell Wax and Bee Bread, which live in memory, not in the Honey Store.
        identity: The Hive, node and actor a `honey.note_proposed` event is stamped with; the
            composition root names the human as actor, since only a human proposes a note here.
        clock: Decides which wax has expired and what "recent" Bee Bread is, and stamps events.
        retriever: Answers a folder's search, under the same reader filter as every query; None
            for a browser that only lists, reads and proposes, which then needs no model binding.
    """

    store: HoneyStore
    sources: BrowseSources
    identity: HoneyIdentity
    clock: Clock
    retriever: HoneyRetriever | None = None
