"""Define AllowedBinding, SeatReservation, ForageGrant, ForageRequest and RoyalReserve.

The **shared pool** (Hive Stand seats, hosted seats and spend) is divided by the Queen (the
central orchestrator) alone, and a `ForageGrant` is what a Warden (a Cell's always-on supervisor)
receives from it: a lease with an expiry naming which bindings it may hand its sub-bees
(`AllowedBinding`: a model slot, the Forage map source it may draw on, and the most effort it may
ask of the model), how many shared seats it holds (`SeatReservation`), and its token and spend
budgets. A Warden spends within its grant without asking; `ForageRequest` is how it asks for more,
naming the dimension (`ForageRequestKind`, mirroring `waggle.messages.forage.values.
ForageRequestKind` member for member) and carrying the task's Tempo as an allocator input.
`RoyalReserve` is what the Queen holds back from the shared pool before any grant: seats and
memory for herself, the Attendant (the inbox triage that runs for every supervisor) and the House
Bees, plus a headroom margin -- every field defaults to a sensible value so a manifest that omits
`[forage.reserve]` entirely still gets a safe reserve.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Built by `hivemind.forage.allocate`
    (`ForageGrant`, `RoyalReserve` is one of its inputs) and read by whatever meters a grant (the
    Fanner, `hivemind.llm.fanner`, a later phase 3 step). `hivemind.manifest` embeds `RoyalReserve`
    directly as the model for `[forage.reserve]`. Calls into `hivemind.forage.slots`,
    `hivemind.forage.tempo`, `hivemind.forage.grant_state`, `hivemind.forage.models.sources` and
    `waggle.messages.forage`/`waggle.messages.base` only.

Key invariants:
    - ForageRequestKind's member names and values are identical to
      `waggle.messages.forage.values.ForageRequestKind`'s (a sync test checks it).
    - ForageRequest is never built asking for nothing: `is_empty` (mirroring the wire
      `ForageDelta.is_empty`) is checked by a validator exactly as `waggle.messages.forage.grants.
      ForageRequest` checks its own `wanted` field.
    - ForageGrant.to_wire needs each allowed binding's full ModelSource (to build the wire
      `SourceRef`), so it takes a `source_id -> ModelSource` mapping rather than looking sources up
      itself; ForageGrant carries no reference to a `ForageMap`.
    - RoyalReserve.headroom_fraction is in [0, 1): a reserve that held back *all* remaining
      capacity would make every grant computation moot.

See Also:
    - .claude/roadmap.md step 3.12 for the field-by-field description this module implements.
    - .claude/codingrules.md section 8.10 for grants, the Royal Reserve and the shared pool.
    - .claude/codingrules.md Appendix C, "Forage grant" row, for the GrantState this model carries.
    - waggle.messages.forage.grants for GrantIssued, the wire form ForageGrant converts to and from.
    - hivemind.forage.allocate for grant(), the pure function that builds a ForageGrant.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage.grant_state import GrantState
from hivemind.forage.models.sources import ModelSource
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.forage.tempo import Tempo
from waggle.messages.base import (
    MAX_REASON_CHARS,
    MAX_SUB_BEES_ON_WIRE,
    CellIdField,
    GrantIdField,
    TaskIdField,
    UtcDatetime,
    WardenIdField,
)
from waggle.messages.forage import AllowedBinding as WireAllowedBinding
from waggle.messages.forage import GrantIssued as WireGrantIssued
from waggle.messages.forage import SeatReservation as WireSeatReservation
from waggle.messages.forage.grants import MAX_ALLOWED_BINDINGS, MAX_SEAT_RESERVATIONS
from waggle.messages.forage.values import (
    MAX_MODEL_GRADE,
    MAX_SOURCE_ID_CHARS,
    MIN_MODEL_GRADE,
)
from waggle.messages.forage.values import ForageRequestKind as WireForageRequestKind

DEFAULT_RESERVE_SEATS = 1  # Enough for the Queen's own awake episode without contending for one.
DEFAULT_RESERVE_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MiB: the Attendant and House Bees are light.
DEFAULT_RESERVE_HEADROOM_FRACTION = 0.1  # A 10% margin absorbs measurement drift between reports.

__all__ = [
    "DEFAULT_RESERVE_HEADROOM_FRACTION",
    "DEFAULT_RESERVE_MEMORY_BYTES",
    "DEFAULT_RESERVE_SEATS",
    "AllowedBinding",
    "ForageGrant",
    "ForageRequest",
    "ForageRequestKind",
    "RoyalReserve",
    "SeatReservation",
]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# A Forage map source id, the same bound the wire form uses, imported so a manifest entry and a
# grant can never disagree on the limit.
_SourceId = Annotated[str, Field(max_length=MAX_SOURCE_ID_CHARS)]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class ForageRequestKind(Enum):
    """Which dimension of shared Forage a ForageRequest asks for; mirrors the wire enum exactly."""

    SHARED_SEATS = "SHARED_SEATS"
    SPEND = "SPEND"
    BINDING = "BINDING"  # A higher-grade binding for a slot.
    SUB_BEES = "SUB_BEES"

    @classmethod
    def from_wire(cls, wire: WireForageRequestKind) -> ForageRequestKind:
        """Build a ForageRequestKind from the wire form on a `forage.request` message."""
        return cls(wire.value)

    def to_wire(self) -> WireForageRequestKind:
        """Build the wire form this ForageRequestKind carries on a `forage.request` message."""
        return WireForageRequestKind(self.value)


# ──────────────────────────────────────────────────────────────────────────────
# Grant-side values
# ──────────────────────────────────────────────────────────────────────────────


class AllowedBinding(BaseModel):
    """One entry of a grant's allowed bindings: a slot, the source it may use, its effort cap."""

    model_config = _MODEL_CONFIG

    slot: ModelSlot = Field(description="The model slot this binding fills.")
    source_id: _SourceId = Field(description="The Forage map source the slot may draw on.")
    max_effort: Effort = Field(description="The most effort the binding may ask of the model.")

    @classmethod
    def from_wire(cls, wire: WireAllowedBinding) -> AllowedBinding:
        """Build an AllowedBinding from the wire form, keeping only the source's id.

        Args:
            wire: The wire `AllowedBinding`, whose full `SourceRef` the receiver would otherwise
                have to keep re-synchronising; only the id survives on this side (docs/waggle/
                spec.md section 8.4), resolved against the receiver's own Forage map.
        """
        return cls(
            slot=ModelSlot.from_wire(wire.slot),
            source_id=wire.source.source_id,
            max_effort=Effort.from_wire(wire.max_effort),
        )

    def to_wire(self, source: ModelSource) -> WireAllowedBinding:
        """Build the wire form, resolving this binding's source_id against `source`.

        Args:
            source: The full `ModelSource` this binding names; its `source_id` must equal this
                binding's own, since the caller is expected to have looked it up by that id.

        Returns:
            The equivalent wire `AllowedBinding`, carrying `source`'s full `SourceRef`.

        Raises:
            ValueError: `source.source_id` does not match this binding's `source_id`.
        """
        if source.source_id != self.source_id:
            raise ValueError(
                f"AllowedBinding names source {self.source_id!r}, but {source.source_id!r} was "
                "given for the wire conversion."
            )
        return WireAllowedBinding(
            slot=self.slot.to_wire(),
            source=source.source_ref(),
            max_effort=self.max_effort.to_wire(),
        )


class SeatReservation(BaseModel):
    """Seats reserved on one shared source, as the Fanner enforces them; mirrors the wire form."""

    model_config = _MODEL_CONFIG

    source_id: _SourceId = Field(description="The Forage map source the seats are reserved on.")
    seats: Annotated[int, Field(ge=0)] = Field(description="Concurrent requests reserved.")
    requests_per_minute: Annotated[int, Field(ge=0)] | None = Field(
        default=None,
        description="Requests per minute reserved on a hosted provider; None when not metered "
        "that way.",
    )
    tokens_per_minute: Annotated[int, Field(ge=0)] | None = Field(
        default=None,
        description="Tokens per minute reserved on a hosted provider; None when not metered "
        "that way.",
    )

    @classmethod
    def from_wire(cls, wire: WireSeatReservation) -> SeatReservation:
        """Build a SeatReservation from the wire form on a `GrantIssued` message."""
        return cls(
            source_id=wire.source_id,
            seats=wire.seats,
            requests_per_minute=wire.requests_per_minute,
            tokens_per_minute=wire.tokens_per_minute,
        )

    def to_wire(self) -> WireSeatReservation:
        """Build the wire form this SeatReservation carries on a `GrantIssued` message."""
        return WireSeatReservation(
            source_id=self.source_id,
            seats=self.seats,
            requests_per_minute=self.requests_per_minute,
            tokens_per_minute=self.tokens_per_minute,
        )


class RoyalReserve(BaseModel):
    """What the Queen holds back from the shared pool before any grant.

    Every field defaults to a sensible value so a manifest that omits `[forage.reserve]` entirely
    still gets a safe reserve (roadmap step 3.12).
    """

    model_config = _MODEL_CONFIG

    seats: Annotated[int, Field(ge=0)] = Field(
        default=DEFAULT_RESERVE_SEATS,
        description="Shared seats held back for the Queen, the Attendant and the House Bees.",
    )
    memory_bytes: Annotated[int, Field(ge=0)] = Field(
        default=DEFAULT_RESERVE_MEMORY_BYTES,
        description="Memory held back on the Hive Stand for the same, in bytes.",
    )
    headroom_fraction: Annotated[float, Field(ge=0, lt=1)] = Field(
        default=DEFAULT_RESERVE_HEADROOM_FRACTION,
        description="An extra margin subtracted from whatever remains, as a fraction in [0, 1), "
        "absorbing drift between capacity reports.",
    )


class ForageGrant(BaseModel):
    """The shared half of Forage a Warden holds: a lease with an expiry and a reason.

    `state` (GrantState) is a Forage-only concept the wire form does not carry (Appendix C's
    "Forage grant" row); `from_wire` always starts a freshly received grant at `GrantState.ISSUED`.
    """

    model_config = _MODEL_CONFIG

    id: GrantIdField = Field(description="The grant's identity, stable across revisions.")
    holder: WardenIdField = Field(description="The Warden that holds it.")
    cell_id: CellIdField = Field(description="The Cell the holder runs.")
    task_id: TaskIdField | None = Field(
        default=None,
        description="The goal or task whose caps sized the budgets; None for a standing grant.",
    )
    revision: Annotated[int, Field(ge=0)] = Field(
        default=0, description="0 on first issue, incremented on every change."
    )
    allowed: tuple[AllowedBinding, ...] = Field(
        default=(), max_length=MAX_ALLOWED_BINDINGS, description="Bindings the holder may use."
    )
    seats: tuple[SeatReservation, ...] = Field(
        default=(),
        max_length=MAX_SEAT_RESERVATIONS,
        description="Reservations the Fanner enforces.",
    )
    token_budget: Annotated[int, Field(ge=0)] = Field(description="Total tokens the grant allows.")
    spend_budget: Annotated[float, Field(ge=0)] = Field(description="Total spend the grant allows.")
    tokens_spent: Annotated[int, Field(ge=0)] = Field(
        default=0, description="Tokens already consumed."
    )
    spent: Annotated[float, Field(ge=0)] = Field(default=0.0, description="Spend already consumed.")
    max_sub_bees: Annotated[int, Field(ge=0, le=MAX_SUB_BEES_ON_WIRE)] = Field(
        description="Sub-bees the holder may run under this grant."
    )
    expires_at: UtcDatetime = Field(
        description="When the grant returns to the pool unless renewed by heartbeat."
    )
    reason: Annotated[str, Field(max_length=MAX_REASON_CHARS)] = Field(
        description="Why these terms: the allocator's decision."
    )
    state: GrantState = Field(
        default=GrantState.ISSUED, description="Where this grant is in its lifecycle."
    )

    @classmethod
    def from_wire(cls, wire: WireGrantIssued) -> ForageGrant:
        """Build a ForageGrant from a `GrantIssued` message, starting at GrantState.ISSUED."""
        return cls(
            id=wire.grant_id,
            holder=wire.holder,
            cell_id=wire.cell_id,
            task_id=wire.task_id,
            revision=wire.revision,
            allowed=tuple(AllowedBinding.from_wire(binding) for binding in wire.allowed),
            seats=tuple(SeatReservation.from_wire(seat) for seat in wire.seats),
            token_budget=wire.token_budget,
            spend_budget=wire.spend_budget,
            tokens_spent=wire.tokens_spent,
            spent=wire.spent,
            max_sub_bees=wire.max_sub_bees,
            expires_at=wire.expires_at,
            reason=wire.reason,
        )

    def to_wire(self, sources: Mapping[str, ModelSource]) -> WireGrantIssued:
        """Build the `GrantIssued` message this grant's current terms carry.

        Args:
            sources: Every source named by `self.allowed`, keyed by `source_id`, so each
                binding's wire form can carry a full `SourceRef` (`AllowedBinding.to_wire`).

        Returns:
            The equivalent `GrantIssued`.
        """
        return WireGrantIssued(
            grant_id=self.id,
            holder=self.holder,
            cell_id=self.cell_id,
            task_id=self.task_id,
            revision=self.revision,
            allowed=tuple(binding.to_wire(sources[binding.source_id]) for binding in self.allowed),
            seats=tuple(seat.to_wire() for seat in self.seats),
            token_budget=self.token_budget,
            spend_budget=self.spend_budget,
            tokens_spent=self.tokens_spent,
            spent=self.spent,
            max_sub_bees=self.max_sub_bees,
            expires_at=self.expires_at,
            reason=self.reason,
        )


class ForageRequest(BaseModel):
    """A Warden's ask for shared Forage beyond its standing grant, with a reason and a Tempo.

    Flattens the wire form's `wanted: ForageDelta` into this model's own fields directly: forage
    names only eleven model concepts for roadmap step 3.12, and a `ForageDelta` mirror is not one
    of them, so the delta's shape is inlined rather than given a twelfth name.
    """

    model_config = _MODEL_CONFIG

    grant_id: GrantIdField = Field(description="The grant the Warden wants extended.")
    kind: ForageRequestKind = Field(description="Which dimension is asked for.")
    task_id: TaskIdField | None = Field(
        default=None,
        description="The task the extra Forage serves; None for the Warden's own needs.",
    )
    seats: Annotated[int, Field(ge=0)] = Field(default=0, description="Shared seats wanted.")
    source_id: _SourceId | None = Field(
        default=None, description="Which shared source the seats are on; None when none are asked."
    )
    spend: Annotated[float, Field(ge=0)] = Field(default=0.0, description="Spend wanted.")
    tokens: Annotated[int, Field(ge=0)] = Field(default=0, description="Tokens wanted.")
    sub_bees: Annotated[int, Field(ge=0, le=MAX_SUB_BEES_ON_WIRE)] = Field(
        default=0, description="Extra concurrent sub-bees wanted."
    )
    slot: ModelSlot | None = Field(
        default=None, description="The slot a BINDING request is for; None for every other kind."
    )
    minimum_grade: Annotated[int, Field(ge=MIN_MODEL_GRADE, le=MAX_MODEL_GRADE)] | None = Field(
        default=None,
        description="The least model grade a BINDING request accepts; None for every other kind.",
    )
    tempo: Tempo = Field(description="The requesting task's Tempo, an allocator input.")
    reason: Annotated[str, Field(max_length=MAX_REASON_CHARS)] = Field(
        description="Why the Warden needs it."
    )

    @property
    def is_empty(self) -> bool:
        """Whether this request asks for nothing at all, mirroring the wire `ForageDelta.is_empty`.

        Returns:
            True when every count and spend is zero and neither a slot nor a grade is named;
            `source_id` alone asks nothing, so it does not count.
        """
        return (
            self.seats == 0
            and self.spend == 0.0
            and self.tokens == 0
            and self.sub_bees == 0
            and self.slot is None
            and self.minimum_grade is None
        )

    @model_validator(mode="after")
    def _wanted_is_not_empty(self) -> ForageRequest:
        """Reject a request that asks for nothing, exactly as the wire form does."""
        if self.is_empty:
            raise ValueError(
                f"A {self.kind.value} ForageRequest must want at least one non-zero field."
            )
        return self
