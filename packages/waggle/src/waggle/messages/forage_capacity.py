"""Define the forage family's capacity and plan values: local sources, pool usage, ceilings, chains.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Forage is
the Hive's capacity as data, and the local half of it is what sits physically on one Cell (a
unit of compute): the model servers running there, the models they have loaded, and what the
Cell's Warden (its always-on supervisor) uses of that local pool under ceilings the Queen (the
central orchestrator) set once. The value models here are the wire forms of that local half plus
the unit a hosting plan is made of: ``LocalSourceReport`` and ``ModelServerReport`` (what a
capacity report says a Cell serves), ``LocalPoolUsage`` and ``CeilingsReport`` (usage against
ceilings, field for field), and ``SourceChain`` and ``SlotPlan`` (a primary source with its
fallbacks, the unit the Fanner, the seat meter every model call passes through, spills along).
The enums are the closed sets the capacity and hosting messages carry. The grant-side values
live in ``waggle.messages.forage_values`` and the messages in ``waggle.messages.forage`` and
``waggle.messages.forage_hosting``, split out by responsibility so every file stays under the
codingrules 5.1 size limit. Every bound is a named constant here; the number, not the name, is
normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.forage_hosting; built by
    a Warden or a Pollen Packet (the thin gateway on an enrolled device) for a capacity report
    and by the Queen for ceilings and plans; calls into waggle.messages.base and
    waggle.messages.forage_values (SourceRef and the shared id bounds) only.

Key invariants:
    - Every value model is frozen and forbids extras through VALUE_MODEL_CONFIG, like a
      message, but none subclasses WaggleMessage, so none can ever be registered as a kind.
    - A LocalSourceReport never has more seats free than it has in total, so the allocator
      subtracts without checking.
    - LocalPoolUsage and CeilingsReport share their dimension names, so usage against a ceiling
      is a field-for-field comparison.

See Also:
    - docs/waggle/spec.md section 8.4 for the normative fields, bounds and validators.
    - waggle.messages.forage_values for SourceRef and the grant-side values.
    - waggle.messages.reports for HostCapacityReport, the host half of a capacity report.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import (
    MAX_SLOT_CHARS,
    MAX_SUB_BEES_ON_WIRE,
    SLOT_PATTERN,
    VALUE_MODEL_CONFIG,
)
from waggle.messages.forage_values import MAX_MODEL_CHARS, MAX_SOURCE_ID_CHARS, SourceRef

MAX_SERVER_ID_CHARS = 128  # The manifest key of a local model server definition: a short handle.
MAX_SOURCES_PER_SERVER = 64  # Models one server can have loaded at once; bounds a report's size.
MAX_LOADABLE_SOURCES = 64  # Source ids a Warden may load on its own; one per model it may pull.
MAX_FALLBACKS = 8  # Fallbacks per chain; the Fanner tries each in turn, so a long chain is slow.

__all__ = [
    "MAX_FALLBACKS",
    "MAX_LOADABLE_SOURCES",
    "MAX_SERVER_ID_CHARS",
    "MAX_SOURCES_PER_SERVER",
    "CapacityTrigger",
    "CeilingsReport",
    "HostingMode",
    "LocalPoolUsage",
    "LocalSourceReport",
    "ModelServerReport",
    "SlotPlan",
    "SourceChain",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class CapacityTrigger(Enum):
    """Why a forage.capacity_report was sent."""

    PROVISIONED = "PROVISIONED"  # A Virtual Cell just came up.
    ENROLLED = "ENROLLED"  # A Real Cell just joined the Swarm.
    CHANGED = "CHANGED"  # A figure moved enough to matter.
    RECONNECTED = "RECONNECTED"  # The link came back; the Queen's copy may be stale.
    PERIODIC = "PERIODIC"  # The timer fired.


class HostingMode(Enum):
    """Where a Cell's bees get their models (forage.hosting_decided)."""

    SHARED_ONLY = "SHARED_ONLY"  # Every call goes to the shared pool.
    LOCAL_FIRST = "LOCAL_FIRST"  # The Cell's own servers first, the shared pool as fallback.
    LOCAL_ONLY = "LOCAL_ONLY"  # Never off the Cell; mandatory for a NIGHT_VEIL Cell.


# ──────────────────────────────────────────────────────────────────────────────
# Value models
# ──────────────────────────────────────────────────────────────────────────────


class LocalSourceReport(BaseModel):
    """One model loaded on a model server on the Cell: the local half of a Forage map source."""

    model_config = VALUE_MODEL_CONFIG

    model: str = Field(max_length=MAX_MODEL_CHARS, description="The Forage map model id.")
    seats_total: int = Field(ge=0, description="Concurrent requests the server allows.")
    seats_free: int = Field(
        ge=0, description="Concurrent requests the server has free; at most seats_total."
    )
    context_window: int = Field(ge=0, description="The model's context window, in tokens.")
    vram_bytes: int = Field(ge=0, description="VRAM the loaded model occupies.")
    tokens_per_s: Annotated[float, Field(ge=0)] | None = Field(
        description="Measured generation speed; None until measured."
    )

    @model_validator(mode="after")
    def _free_within_total(self) -> LocalSourceReport:
        """Reject more free seats than the server allows."""
        # The allocator subtracts free from total without a guard, so the report must agree
        # with itself, as GpuReport and HostCapacityReport do for their free figures.
        if self.seats_free > self.seats_total:
            raise ValueError(
                f"Source {self.model!r} reports {self.seats_free} free seats, more than its "
                f"total of {self.seats_total}."
            )
        return self


class ModelServerReport(BaseModel):
    """A model server running on the Cell, with the models it serves.

    The server is named by its manifest key and the receiver resolves that to a provider and an
    address, so no URL crosses the wire.
    """

    model_config = VALUE_MODEL_CONFIG

    server_id: str = Field(
        max_length=MAX_SERVER_ID_CHARS,
        description="The manifest key of the local model server definition; the receiver "
        "resolves it to a provider and address.",
    )
    sources: tuple[LocalSourceReport, ...] = Field(
        max_length=MAX_SOURCES_PER_SERVER, description="The models it serves."
    )


class LocalPoolUsage(BaseModel):
    """The Warden's usage against its ceilings plus the seats it has exported.

    Every field mirrors one CeilingsReport dimension, so usage against a ceiling is a
    field-for-field comparison.
    """

    model_config = VALUE_MODEL_CONFIG

    sub_bees_active: int = Field(ge=0, description="Sub-bees running on the local pool now.")
    model_vram_bytes: int = Field(ge=0, description="VRAM the Warden's loaded models occupy.")
    model_disk_bytes: int = Field(ge=0, description="Disk the Warden's model files occupy.")
    seats_exported: int = Field(
        ge=0, description="Seats on the Cell's servers lent to the shared pool."
    )


class CeilingsReport(BaseModel):
    """The wire form of Ceilings: what a Warden may use of its own Cell without asking."""

    model_config = VALUE_MODEL_CONFIG

    max_sub_bees: int = Field(
        ge=0,
        le=MAX_SUB_BEES_ON_WIRE,
        description="Concurrent sub-bees the Warden may run from its local pool.",
    )
    model_vram_bytes: int = Field(ge=0, description="VRAM the Warden may fill with models.")
    model_disk_bytes: int = Field(ge=0, description="Disk the Warden may fill with model files.")
    loadable_sources: tuple[Annotated[str, Field(max_length=MAX_SOURCE_ID_CHARS)], ...] = Field(
        max_length=MAX_LOADABLE_SOURCES,
        description="Source ids the Warden may load on its own, as SourceRef.source_id.",
    )
    exportable_seats: int = Field(
        ge=0, description="Seats on the Cell's servers the Warden may lend to the shared pool."
    )


class SourceChain(BaseModel):
    """A primary source and its fallback chain: the unit the Fanner spills along."""

    model_config = VALUE_MODEL_CONFIG

    primary: SourceRef = Field(description="The source every call tries first.")
    fallbacks: tuple[SourceRef, ...] = Field(
        max_length=MAX_FALLBACKS,
        description="The sources tried in order when the primary has no seat or is down.",
    )


class SlotPlan(BaseModel):
    """One named slot's entry in a hosting plan."""

    model_config = VALUE_MODEL_CONFIG

    slot: str = Field(
        max_length=MAX_SLOT_CHARS,
        pattern=SLOT_PATTERN,
        description="The model slot the chain serves.",
    )
    chain: SourceChain = Field(description="The primary and fallbacks for that slot.")
