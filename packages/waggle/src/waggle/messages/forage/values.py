"""Define the forage family's grant-side values: sources, bindings, seats and the request delta.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Forage is
the Hive's capacity as data, in several dimensions and never one number: the Queen (the central
orchestrator) divides what is shared by grant, a lease on shared model capacity held by a Warden
(the always-on supervisor of one Cell, a unit of compute). The value models here are the pieces
a grant, and a request for more, are made of: ``SourceRef`` names one entry of the Forage map
(every source that can serve a model) without its live figures; ``AllowedBinding`` pairs a model
slot (the named role a model is bound to) with a source and an effort cap; ``SeatReservation``
holds seats on one shared server or hosted provider, where the hosted equivalent of a seat is
requests and tokens per minute; ``ForageDelta`` is the delta a Warden asks for and the Queen
grants, one shape for both so a partial grant has the shape of the ask. The enums are the closed
sets those models and the grant messages carry. The family's other values (a Cell's capacity,
its ceilings and the chains of a hosting plan) live in ``waggle.messages.forage.capacity``, and
its messages in ``waggle.messages.forage.grants`` and ``waggle.messages.forage.hosting``, each split
out by responsibility so every file stays under the codingrules 5.1 size limit. Every bound is a
named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.forage.grants (the grant
    messages) and waggle.messages.forage.capacity (SourceRef, for a chain); built by the Queen's
    allocator and read by every Warden's Fanner (the seat meter every model call passes
    through); calls into waggle.messages.base only.

Key invariants:
    - Every value model is frozen and forbids extras through VALUE_MODEL_CONFIG, like a
      message, but none subclasses WaggleMessage, so none can ever be registered as a kind.
    - A ForageDelta with every count zero, no slot and no grade is empty (``is_empty``): the
      one test forage.request and forage.reply apply to it.
    - No module of another family is imported here, so no family imports another through it.

See Also:
    - docs/waggle/spec.md section 8.4 for the normative fields, bounds and validators.
    - waggle.messages.forage.capacity for the family's capacity and plan values.
    - waggle.messages.forage.grants and waggle.messages.forage.hosting for the messages that carry
      these values.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field

from waggle.messages.base import (
    MAX_SLOT_CHARS,
    MAX_SUB_BEES_ON_WIRE,
    SLOT_PATTERN,
    VALUE_MODEL_CONFIG,
    CellIdField,
)

MAX_SOURCE_ID_CHARS = 128  # A Forage map entry key names a provider and a model: a short handle.
MAX_PROVIDER_CHARS = 64  # A manifest provider key (anthropic, ollama_local, ...): one word.
MAX_MODEL_CHARS = 128  # A model id as the map names it; some vendors' ids carry a long date tag.
MIN_MODEL_GRADE = 1  # The Forage map grades models from 1, the weakest that can hold a slot.
MAX_MODEL_GRADE = 5  # To 5, the strongest; a binding request names the least grade it accepts.

__all__ = [
    "MAX_MODEL_CHARS",
    "MAX_MODEL_GRADE",
    "MAX_PROVIDER_CHARS",
    "MAX_SOURCE_ID_CHARS",
    "MIN_MODEL_GRADE",
    "AllowedBinding",
    "Effort",
    "ForageDelta",
    "ForageOutcome",
    "ForageRequestKind",
    "RevocationCause",
    "SeatReservation",
    "SourceRef",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class Effort(Enum):
    """A model's effort setting, fixed per binding by the grant that allows it."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RevocationCause(Enum):
    """Which rule revoked a grant (forage.grant_revoked)."""

    EXPIRED = "EXPIRED"  # The expiry passed with no heartbeat renewing it.
    HOLDER_OFFLINE = "HOLDER_OFFLINE"  # The holder stopped answering.
    RECLAIMED = "RECLAIMED"  # The Queen took it back for a higher-priority need.
    RELEASED = "RELEASED"  # The holder gave it back.
    STING_CUT = "STING_CUT"  # The human's per-Cell emergency disconnect.


class ForageRequestKind(Enum):
    """Which dimension of shared Forage a forage.request asks for."""

    SHARED_SEATS = "SHARED_SEATS"
    SPEND = "SPEND"
    BINDING = "BINDING"  # A higher-grade binding for a slot.
    SUB_BEES = "SUB_BEES"


class ForageOutcome(Enum):
    """How the Queen answered a forage.request: in full, in part, or not at all."""

    GRANTED = "GRANTED"
    PARTIAL = "PARTIAL"
    DENIED = "DENIED"


# A model slot field, as the catalogue conventions fix it: the UPPER_SNAKE name of a ModelSlot
# member, travelling as a string because hivemind's enum never crosses into waggle.
_Slot = Annotated[str, Field(max_length=MAX_SLOT_CHARS, pattern=SLOT_PATTERN)]
# A Forage map entry key, the same shape wherever a source is named by id alone.
_SourceId = Annotated[str, Field(max_length=MAX_SOURCE_ID_CHARS)]


# ──────────────────────────────────────────────────────────────────────────────
# Value models
# ──────────────────────────────────────────────────────────────────────────────


class SourceRef(BaseModel):
    """One Forage map source by name, without its live figures.

    Carried by every AllowedBinding and every SourceChain; the receiver resolves it against its
    own copy of the map, so the figures that change (seats free, speed) never ride along.
    """

    model_config = VALUE_MODEL_CONFIG

    source_id: _SourceId = Field(description="The Forage map entry key.")
    provider: str = Field(max_length=MAX_PROVIDER_CHARS, description="The manifest provider name.")
    model: str = Field(max_length=MAX_MODEL_CHARS, description="The model id.")
    host_cell_id: CellIdField | None = Field(
        description="The Cell whose server serves it; None for a hosted provider."
    )


class AllowedBinding(BaseModel):
    """One entry of a grant's allowed bindings: a slot, the source it may use, its effort cap."""

    model_config = VALUE_MODEL_CONFIG

    slot: _Slot = Field(description="The model slot the binding fills.")
    source: SourceRef = Field(description="The source the slot may draw on under this grant.")
    max_effort: Effort = Field(description="The most effort the binding may ask of the model.")


class SeatReservation(BaseModel):
    """Seats reserved on one shared server or hosted provider, as the Fanner enforces them.

    The hosted equivalent of a seat is requests and tokens per minute, so both forms travel here
    and a reservation on a hosted provider sets the two rates instead of, or as well as, seats.
    """

    model_config = VALUE_MODEL_CONFIG

    source_id: _SourceId = Field(description="The Forage map entry key the seats are on.")
    seats: int = Field(ge=0, description="Concurrent requests reserved on the source.")
    requests_per_minute: Annotated[int, Field(ge=0)] | None = Field(
        description="Requests per minute reserved on a hosted provider; None when the source "
        "is not metered that way."
    )
    tokens_per_minute: Annotated[int, Field(ge=0)] | None = Field(
        description="Tokens per minute reserved on a hosted provider; None when the source is "
        "not metered that way."
    )


class ForageDelta(BaseModel):
    """The delta a Warden wants, or the Queen granted; one shape serves both.

    forage.request carries it as the ask and forage.reply as the answer, so a partial grant has
    exactly the shape of the ask and the holder folds it in without translating.
    """

    model_config = VALUE_MODEL_CONFIG

    seats: int = Field(default=0, ge=0, description="Shared seats wanted or granted.")
    source_id: _SourceId | None = Field(
        description="Which shared source the seats are on, as SourceRef.source_id; None when "
        "no seats are asked for."
    )
    spend: float = Field(
        default=0.0, ge=0, description="Spend wanted or granted, in the manifest's currency."
    )
    tokens: int = Field(default=0, ge=0, description="Tokens wanted or granted.")
    sub_bees: int = Field(
        default=0,
        ge=0,
        le=MAX_SUB_BEES_ON_WIRE,
        description="Extra concurrent sub-bees wanted or granted.",
    )
    slot: _Slot | None = Field(
        description="The slot a BINDING request is for; None for every other kind."
    )
    minimum_grade: Annotated[int, Field(ge=MIN_MODEL_GRADE, le=MAX_MODEL_GRADE)] | None = Field(
        description="The least model grade a BINDING request accepts, 1 to 5; None for every "
        "other kind."
    )

    @property
    def is_empty(self) -> bool:
        """Whether the delta asks for, or grants, nothing at all.

        Returns:
            True when every count is zero, the spend is zero and neither a slot nor a grade is
            named; ``source_id`` alone asks nothing, so it does not count.
        """
        # forage.request refuses an empty ask and forage.reply requires an empty grant on a
        # denial; both read the same predicate so the two ends can never disagree on "nothing".
        return (
            self.seats == 0
            and self.spend == 0.0
            and self.tokens == 0
            and self.sub_bees == 0
            and self.slot is None
            and self.minimum_grade is None
        )
