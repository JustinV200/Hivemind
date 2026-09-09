"""Define LocalPool, Ceilings, SourceChain, SlotPlan and HostingPlan: the local half of Forage.

A **local pool** is everything physically on one Cell (a unit of compute): its cores, memory,
disk, VRAM and any seats on a model server running on it. The Cell's Warden (its always-on
supervisor) owns this outright and divides it among its own sub-bees with the same allocator the
Queen uses on the shared pool, so `LocalPool` mirrors `HostCapacity` and `Seat`
(`hivemind.forage.models.capacity`) plus a small reserve for the Warden's own awake mode and
Patrol (a Real Cell's Warden with no active bees, reviewing read-only on a schedule) -- the same
shape as the Queen's `RoyalReserve`, reused here because both describe "what this supervisor holds
back from its own pool before granting anything," just scoped to one Cell instead of the whole
Hive. `Ceilings` are the Queen's only say over a local pool, set once when a Warden moves onto a
device: how many sub-bees it may run, how much VRAM and disk it may give to models, which Forage
map entries it may load, and how many of its own seats the Queen may borrow back for other Cells.
`SourceChain` (a primary source and its fallbacks, named by id) and `SlotPlan` (one slot's chain)
are the units a `HostingPlan` is built from: per Cell, for every named slot a primary source and a
fallback chain, plus a default chain for slots the plan does not name -- "a hosting plan, not a
hosting flag" (codingrules section 8.10), written by the Queen with a reason.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). `hivemind.wardens.local_pool` (a
    later phase 3 step) owns a `LocalPool` and runs `hivemind.forage.allocate`'s allocator over
    it; the Queen writes a `Ceilings` and a `HostingPlan` per Cell. Calls into
    `hivemind.forage.slots`, `hivemind.forage.models.capacity`, `hivemind.forage.models.grants`
    and `hivemind.forage.models.sources`, plus `waggle.messages.forage`/`waggle.messages.base`.

Key invariants:
    - SourceChain and SlotPlan name sources by id only, exactly like AllowedBinding
      (`hivemind.forage.models.grants`): the receiver resolves ids against its own Forage map, so
      to_wire takes a `source_id -> ModelSource` mapping to build the wire form's full SourceRefs.
    - HostingPlan never names one slot twice, matching the wire `PlanWritten`'s own rule.
    - Ceilings mirrors `waggle.messages.forage.capacity.CeilingsReport` field for field, so a
      Warden's usage report and its ceilings compare directly without translation.

See Also:
    - .claude/roadmap.md step 3.12 for the field-by-field description this module implements.
    - .claude/codingrules.md section 8.10 for local pools, ceilings and hosting plans.
    - waggle.messages.forage.capacity for CeilingsReport, SourceChain and SlotPlan, the wire forms
      Ceilings, SourceChain and SlotPlan here convert to and from.
    - hivemind.forage.models.grants for RoyalReserve, the shape LocalPool's own reserve reuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage.models.capacity import HostCapacity, Seat
from hivemind.forage.models.grants import RoyalReserve
from hivemind.forage.models.sources import ModelSource
from hivemind.forage.slots import ModelSlot
from waggle.messages.base import MAX_REASON_CHARS, MAX_SUB_BEES_ON_WIRE, CellIdField
from waggle.messages.forage import CeilingsReport as WireCeilingsReport
from waggle.messages.forage import SlotPlan as WireSlotPlan
from waggle.messages.forage import SourceChain as WireSourceChain
from waggle.messages.forage.capacity import MAX_FALLBACKS, MAX_LOADABLE_SOURCES
from waggle.messages.forage.hosting import MAX_SLOT_PLANS
from waggle.messages.forage.values import MAX_SOURCE_ID_CHARS

__all__ = [
    "Ceilings",
    "HostingPlan",
    "LocalPool",
    "SlotPlan",
    "SourceChain",
]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# A Forage map source id, the same bound the wire form uses.
_SourceId = Annotated[str, Field(max_length=MAX_SOURCE_ID_CHARS)]


class LocalPool(BaseModel):
    """What one Warden owns outright: its Cell's compute minus any model server's footprint.

    "The half a Warden owns: its Cell's HostCapacity minus the footprint of any model server on
    it, the seats that server offers, and a small local reserve for the Warden's own awake mode
    and Patrol" (roadmap step 3.12). `reserve` reuses `RoyalReserve`'s shape (see module
    docstring) rather than inventing a second, identically-shaped model.
    """

    model_config = _MODEL_CONFIG

    host_capacity: HostCapacity = Field(
        description="The Cell's HostCapacity, already reduced by any model server's own footprint."
    )
    local_seats: tuple[Seat, ...] = Field(
        default=(), description="Seat capacity the Cell's own model servers offer."
    )
    reserve: RoyalReserve = Field(
        description="A small reserve for the Warden's own awake mode and Patrol, held back "
        "before its allocator divides the rest among sub-bees."
    )


class Ceilings(BaseModel):
    """What the Queen sets once per device Warden or Nuc: bounds on its own local pool.

    Mirrors `waggle.messages.forage.capacity.CeilingsReport` field for field.
    """

    model_config = _MODEL_CONFIG

    max_sub_bees: Annotated[int, Field(ge=0, le=MAX_SUB_BEES_ON_WIRE)] = Field(
        description="Concurrent sub-bees the Warden may run from its local pool."
    )
    model_vram_bytes: Annotated[int, Field(ge=0)] = Field(
        description="VRAM the Warden may fill with models."
    )
    model_disk_bytes: Annotated[int, Field(ge=0)] = Field(
        description="Disk the Warden may fill with model files."
    )
    loadable_sources: tuple[_SourceId, ...] = Field(
        default=(),
        max_length=MAX_LOADABLE_SOURCES,
        description="Forage map source ids the Warden may load on its own.",
    )
    exportable_seats: Annotated[int, Field(ge=0)] = Field(
        description="Seats on the Cell's own servers the Warden may lend to the shared pool."
    )

    @classmethod
    def from_wire(cls, wire: WireCeilingsReport) -> Ceilings:
        """Build a Ceilings from the wire `CeilingsReport` on a `forage.ceilings_set` message."""
        return cls(
            max_sub_bees=wire.max_sub_bees,
            model_vram_bytes=wire.model_vram_bytes,
            model_disk_bytes=wire.model_disk_bytes,
            loadable_sources=wire.loadable_sources,
            exportable_seats=wire.exportable_seats,
        )

    def to_wire(self) -> WireCeilingsReport:
        """Build the wire `CeilingsReport` this Ceilings carries on a `forage.ceilings_set`."""
        return WireCeilingsReport(
            max_sub_bees=self.max_sub_bees,
            model_vram_bytes=self.model_vram_bytes,
            model_disk_bytes=self.model_disk_bytes,
            loadable_sources=self.loadable_sources,
            exportable_seats=self.exportable_seats,
        )


class SourceChain(BaseModel):
    """A primary Forage map source and its fallback chain, the unit the Fanner spills along.

    Names sources by id only, like `AllowedBinding` (`hivemind.forage.models.grants`); `to_wire`
    resolves each id against a `source_id -> ModelSource` mapping to build the wire form's full
    SourceRefs.
    """

    model_config = _MODEL_CONFIG

    primary: _SourceId = Field(description="The source every call tries first.")
    fallbacks: tuple[_SourceId, ...] = Field(
        default=(),
        max_length=MAX_FALLBACKS,
        description="Sources tried in order when the primary has no seat or is down.",
    )

    @classmethod
    def from_wire(cls, wire: WireSourceChain) -> SourceChain:
        """Build a SourceChain from the wire form, keeping only each source's id."""
        return cls(
            primary=wire.primary.source_id,
            fallbacks=tuple(source.source_id for source in wire.fallbacks),
        )

    def to_wire(self, sources: Mapping[str, ModelSource]) -> WireSourceChain:
        """Build the wire form, resolving every id in this chain against `sources`.

        Args:
            sources: Every source this chain names, keyed by `source_id`.

        Returns:
            The equivalent wire `SourceChain`.
        """
        return WireSourceChain(
            primary=sources[self.primary].source_ref(),
            fallbacks=tuple(sources[source_id].source_ref() for source_id in self.fallbacks),
        )


class SlotPlan(BaseModel):
    """One named slot's entry in a hosting plan: the slot and its SourceChain."""

    model_config = _MODEL_CONFIG

    slot: ModelSlot = Field(description="The model slot this chain serves.")
    chain: SourceChain = Field(description="The primary and fallbacks for that slot.")

    @classmethod
    def from_wire(cls, wire: WireSlotPlan) -> SlotPlan:
        """Build a SlotPlan from the wire form on a `forage.plan_written` message."""
        return cls(slot=ModelSlot.from_wire(wire.slot), chain=SourceChain.from_wire(wire.chain))

    def to_wire(self, sources: Mapping[str, ModelSource]) -> WireSlotPlan:
        """Build the wire form, resolving this plan's chain against `sources`.

        Args:
            sources: Every source `self.chain` names, keyed by `source_id`.
        """
        return WireSlotPlan(slot=self.slot.to_wire(), chain=self.chain.to_wire(sources))


class HostingPlan(BaseModel):
    """Per Cell, for each slot a primary source and a fallback chain, plus a default.

    "Replaces any single hosting flag" (roadmap step 3.12): written by the Queen from the Forage
    map and ledger, with a reason, rather than one global local-vs-shared switch.
    """

    model_config = _MODEL_CONFIG

    cell_id: CellIdField = Field(description="The Cell this plan is for.")
    revision: Annotated[int, Field(ge=0)] = Field(
        default=0, description="0 for the first plan, incremented on each rewrite."
    )
    slots: tuple[SlotPlan, ...] = Field(
        default=(),
        max_length=MAX_SLOT_PLANS,
        description="Per named slot, its chain; slot names unique.",
    )
    default: SourceChain = Field(description="The chain used for any slot not named above.")
    reason: Annotated[str, Field(max_length=MAX_REASON_CHARS)] = Field(
        description="Why this plan: free VRAM, seat pressure, distance against Tempo, the need "
        "to survive disconnection, cost."
    )

    @model_validator(mode="after")
    def _slot_names_unique(self) -> HostingPlan:
        """Reject a plan that names one slot twice, matching the wire `PlanWritten`'s own rule."""
        # The Fanner looks a chain up by slot; two entries for one slot would make the lookup
        # depend on order, and the writer that produced them has a bug.
        slots = [plan.slot for plan in self.slots]
        if len(set(slots)) != len(slots):
            raise ValueError("HostingPlan slot names must be unique.")
        return self
