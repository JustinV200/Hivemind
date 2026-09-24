"""Define TaintLedger: anything that holds taintable items, and TaintableItem, one item's facts.

The taint label (roadmap step 10.6d) sits on items that live in more than one store: the memory
tables hold Handoffs, episode records and Bee Bread deposits today, and the Honey Store (a sibling
subsystem this package may not import) will hold Nectar and Honey from phase 7. `TaintLedger` is
the one seam both implement, so the setter and the clearer never care which store an item lives
in: `find_taintable` returns every item a `TaintScope` covers that is not already TAINTED,
`read_taintable` returns one item's label, clearance and the text a taint judge reviews, and
`write_taint` replaces a label and records its trail event in the same transaction, checking the
label's transition table against the stored label inside that transaction. `MemoryStore` satisfies
it structurally today; the Honey Store's implementation for `NECTAR` and `HONEY` is phase 7's
(roadmap 7.4 and 7.7), and until it exists nothing labels those kinds.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`.
    Implemented by `hivemind.memory.store.memory.InMemoryMemoryStore` and `hivemind.memory.store.
    sqlite.SqliteMemoryStore`; called only by `hivemind.memory.taint.set.taint_memory` (which
    writes TAINTED) and `hivemind.memory.taint.clear.clear_taint` (which writes CLEARED). Calls
    into `hivemind.cell` (HoneyClearance), `hivemind.pheromone` (MemoryEvent) and this package's
    `marker` and `scope`.

Key invariants:
    - `write_taint` is the only way a label is ever written, and only the setter and the clearer
      call it (a test walks the source tree to hold every module to that).
    - `find_taintable` never returns an item that is already TAINTED, so a second taint of the
      same scope labels only what the first did not.
    - `TaintableItem.content` is the item's own text, whole, for the judge; never truncated here
      (the clearer refuses to judge what it cannot show whole).

See Also:
    - hivemind.memory.store.protocol for MemoryStore, the ledger that exists today.
    - hivemind.memory.taint.state for the transition table `write_taint` checks.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.memory.taint.marker import TaintMarker, TaintTarget
from hivemind.memory.taint.scope import TaintScope
from hivemind.pheromone import MemoryEvent

__all__ = ["TaintLedger", "TaintableItem"]


class TaintableItem(BaseModel):
    """One taintable item as its ledger describes it: address, label, clearance and text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: TaintTarget = Field(description="The item's kind and id.")
    marker: TaintMarker | None = Field(description="Its current label; None if never labelled.")
    clearance: HoneyClearance = Field(description="Its own data-sensitivity label.")
    content: str = Field(
        description="The item's own text, whole, as a taint judge reviews it (a Handoff's "
        "fields, an episode's trigger and decision, an entry's text or payload)."
    )


class TaintLedger(Protocol):
    """Hold taintable items: find a scope's items, read one, and write one's label atomically.

    Implementations must be safe to call concurrently and must check the label's transition table
    against the stored label inside the same transaction that writes the new one.
    """

    async def find_taintable(self, scope: TaintScope) -> tuple[TaintTarget, ...]:
        """Return every item `scope` covers whose label is not TAINTED, oldest first.

        Args:
            scope: Which authors, tasks, kinds and moment the taint covers.

        Returns:
            The targets to label; empty when the scope covers nothing new.
        """
        ...

    async def read_taintable(self, target: TaintTarget) -> TaintableItem:
        """Return one item's label, clearance and reviewable text.

        Args:
            target: The item to read.

        Returns:
            The item as a taint judge and the clearer see it.

        Raises:
            hivemind.memory.errors.TaintTargetNotFoundError: No such item in this ledger.
        """
        ...

    async def write_taint(
        self, target: TaintTarget, marker: TaintMarker, event: MemoryEvent
    ) -> None:
        """Replace `target`'s label with `marker` and record `event`, atomically.

        Args:
            target: The item to label.
            marker: Its new label (TAINTED from the setter, CLEARED from the clearer).
            event: The accompanying `memory.tainted` or `memory.taint_cleared` trail event.

        Raises:
            hivemind.memory.errors.TaintTargetNotFoundError: No such item in this ledger.
            hivemind.memory.errors.InvalidTaintTransitionError: The stored label cannot move to
                `marker.state`; nothing is written.
        """
        ...
