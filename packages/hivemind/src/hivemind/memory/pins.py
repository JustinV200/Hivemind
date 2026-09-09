"""Define Pin and PinSource: facts that never decay, and how one is added to memory.

A Pin is a fact hot state packs first and never drops for recency reasons (codingrules section
8.9: "pins that never decay"; `hivemind.memory.hot_state.packing.assemble` only ever drops a pin
when that one pin, alone, cannot fit the whole budget). `PinSource` records where a Pin came from:
`MANIFEST` (loaded from the Hive Manifest's `[memory] pins` at start) or `RUNTIME` (added while the
Hive is running, by a bee or the human). `add_pin` is the one write path: it builds the
`memory.pinned` trail event and hands both it and the Pin to the `MemoryStore` in one call, so the
row and its event commit together (codingrules section 12).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `add_pin` is called by whichever
    composition root loads manifest pins at start, and by any bee or the human adding a runtime
    one. Calls into hivemind.cell (HoneyClearance), hivemind.memory.context (MemoryContext),
    hivemind.pheromone (MemoryEvent) and waggle only.

Key invariants:
    - Pin.id is an EventId-shaped ULID minted by `waggle.ids.new_event_id`; no dedicated `PinId`
      IdKind exists this phase (codingrules section 4's mirror-or-import rule does not apply here,
      since this is a fresh id, not a wire mirror).
    - add_pin's memory.pinned event's subject_id is the Pin's own id, so every pin's whole history
      (there is only ever one: it is written once) is findable by querying the trail for that id.

See Also:
    - .claude/codingrules.md section 8.9 for "pins that never decay".
    - .claude/codingrules.md section 12 for the same-transaction rule add_pin relies on its store
      to uphold.
    - hivemind.memory.hot_state.packing for assemble, the one reader of Pin via HotStateSources.
    - hivemind.memory.store.protocol for MemoryStore.add_pin/list_pins/remove_pin.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.memory.context import MemoryContext
from hivemind.pheromone import MemoryEvent
from waggle.ids import new_event_id
from waggle.messages.base import EventIdField, UtcDatetime

MAX_PIN_TEXT_CHARS = 2_000  # A pinned fact is a sentence or two, never a document.

__all__ = ["MAX_PIN_TEXT_CHARS", "Pin", "PinSource", "add_pin"]


class PinSource(Enum):
    """Where a Pin came from: loaded at start, or added while the Hive is running."""

    MANIFEST = "MANIFEST"  # Loaded from `[memory] pins` in the Hive Manifest at start.
    RUNTIME = "RUNTIME"  # Added while the Hive is running: a bee or the human pinned it.


class Pin(BaseModel):
    """A fact hot state packs first and (almost) never drops.

    Every Pin carries a HoneyClearance like every other memory-tier row, so `assemble` still
    filters a Royal pin out of a Night Veil bee's prompt even though pins are otherwise privileged
    (codingrules section 8.9: clearance filtering always comes first).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventIdField = Field(
        description="This pin's own id: an EventId-shaped ULID; no PinId kind exists this phase."
    )
    text: str = Field(min_length=1, max_length=MAX_PIN_TEXT_CHARS, description="The pinned fact.")
    clearance: HoneyClearance = Field(description="This pin's data-sensitivity label.")
    source: PinSource = Field(description="Where this pin came from.")
    created_at: UtcDatetime = Field(description="When it was added.")


async def add_pin(pin: Pin, ctx: MemoryContext) -> None:
    """Write `pin` and its `memory.pinned` trail event, atomically.

    Args:
        pin: The pin to add; its id must be new to the store.
        ctx: The store, identity and clock to write with.

    Returns:
        None, once the pin and its event are durably recorded together.
    """
    event = MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind="memory.pinned",
        subject_id=pin.id,
        payload={"source": pin.source.value},
    )
    await ctx.store.add_pin(pin, event)
