"""Define the cell family's Cell Wax messages: a caution proposed, written and cleared.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Cell Wax
is a Queen-written caution about one Cell (a unit of compute: a Virtual Cell the Hive provisions,
or a Real Cell, an existing device borrowed for a task and left exactly as found): anyone may
propose one, only the Queen (the central orchestrator) writes or clears it, and placement (the
Queen's choice of a Cell for a task) treats a BLOCK as exclusion and a CAUTION as a penalty. The
three messages here are that note's life: ``CellWaxProposed`` (any bee to the Queen; a rejection
is a control.error with code hive.memory.wax_rejected), ``CellWaxWritten`` (the full note to the
Cell's Warden, its always-on supervisor, so it holds it even when offline) and ``CellWaxCleared``
(cleared, expired on the sweep, or retired with its destroyed Virtual Cell). A note is identified
by a ``wax_``-prefixed ULID string until an IdKind for it exists. The family's other messages
live in ``waggle.messages.cell`` and ``waggle.messages.cell_leases``, split out by responsibility
so every file stays under the codingrules 5.1 size limit. Every bound is a named constant here;
the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; a proposal is built by any bee and read by the Queen, the other two
    are built by the Queen and read by a Warden; calls into waggle.ids, waggle.messages.base and
    waggle.messages.labels only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A proposal or a written note names its proposing bee exactly when a bee, not the human,
      noticed the caution, so a Worker's proposal relayed by its Warden still routes to an
      awake decision.

See Also:
    - docs/waggle/spec.md section 8.5 for the normative fields, bounds and validators.
    - waggle.messages.cell and waggle.messages.cell_leases for the rest of the family.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, model_validator

from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    TaskIdField,
    UtcDatetime,
    WaggleMessage,
    id_validator,
)
from waggle.messages.labels import HoneyClearance

WAX_ID_PATTERN = r"^wax_[0-9A-HJKMNP-TV-Z]{26}$"  # wax_ plus a Crockford ULID; no IdKind yet.
MIN_WAX_TEXT_CHARS = 1  # A caution always says something.
MAX_WAX_TEXT_CHARS = 4_000  # A paragraph about one Cell, never a report; manifest may cap lower.

__all__ = [
    "MAX_WAX_TEXT_CHARS",
    "MIN_WAX_TEXT_CHARS",
    "WAX_ID_PATTERN",
    "CellWaxCleared",
    "CellWaxProposed",
    "CellWaxWritten",
    "WaxClearCause",
    "WaxDecision",
    "WaxOrigin",
    "WaxSeverity",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class WaxSeverity(Enum):
    """How much a Cell Wax note weighs on placement."""

    NOTE = "NOTE"  # Recorded, no effect on placement.
    CAUTION = "CAUTION"  # A penalty in placement.
    BLOCK = "BLOCK"  # Exclusion from placement; every BLOCK is an awake decision.


class WaxOrigin(Enum):
    """Who noticed the caution a Cell Wax note records."""

    BEE = "BEE"
    PATROL = "PATROL"  # A watching Warden's scheduled review.
    HUMAN = "HUMAN"


class WaxDecision(Enum):
    """Whether the Queen wrote a note by rule or by a model episode."""

    AUTOPILOT = "AUTOPILOT"
    AWAKE = "AWAKE"


class WaxClearCause(Enum):
    """Why a Cell Wax note left the written state."""

    CLEARED = "CLEARED"  # The Queen cleared it.
    EXPIRED = "EXPIRED"  # Its expiry passed on the sweep.
    CELL_RETIRED = "CELL_RETIRED"  # Its Virtual Cell was destroyed.


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A Cell Wax note's id: a plain string until an IdKind exists for it (catalogue conventions).
_WaxId = Annotated[str, Field(pattern=WAX_ID_PATTERN)]
# The caution text, bounded the same way on a proposal and on the written note.
_WaxText = Annotated[str, Field(min_length=MIN_WAX_TEXT_CHARS, max_length=MAX_WAX_TEXT_CHARS)]
# The proposing bee: a Worker or a Warden, never the Queen or a device.
_Proposer = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]


def _check_proposer_matches_origin(
    origin: WaxOrigin, proposer: WorkerId | WardenId | None, message_name: str
) -> None:
    """Raise ValueError unless ``proposer`` is None exactly when ``origin`` is HUMAN."""
    # A human proposes through the Entrance, not as a bee, so no bee id fits; a bee's proposal
    # must name the bee so the Queen routes it to an awake decision. Both directions are
    # checked, and both wax messages apply the same rule, so it lives in one helper.
    if (origin is WaxOrigin.HUMAN) != (proposer is None):
        raise ValueError(
            f"{message_name} proposer must be None exactly when origin is HUMAN, got origin "
            f"{origin.value} with proposer {proposer}."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class CellWaxProposed(WaggleMessage):
    """Propose a Cell Wax note about one Cell (cell.wax_proposed, a request).

    A caution with severity, text, reason, clearance and optional expiry. Only the Queen writes
    it; a rejection is a control.error with code hive.memory.wax_rejected. The proposer equals
    the envelope sender on the first hop (a receiver rule).
    """

    cell_id: CellIdField = Field(description="The Cell the note is about.")
    severity: WaxSeverity = Field(
        description="How much it should weigh; every BLOCK is an awake decision."
    )
    text: _WaxText = Field(description="The caution itself; the manifest may cap it lower.")
    reason: _Reason = Field(description="Why the proposer believes it.")
    clearance: HoneyClearance = Field(
        description="The note's label; anything describing an operator's own machine is C2."
    )
    expires_at: UtcDatetime | None = Field(
        description="When the note expires on its own; None for standing wax."
    )
    origin: WaxOrigin = Field(description="Who noticed it.")
    proposer: _Proposer | None = Field(
        description="The proposing bee, so a Worker's proposal relayed by its Warden still "
        "routes to an awake decision; None exactly when origin is HUMAN. Equals the envelope "
        "sender on the first hop (a receiver rule)."
    )
    task_id: TaskIdField | None = Field(
        description="The task during which it was noticed, for provenance; None when no task "
        "is involved (a Patrol, the human, or the Warden's own work)."
    )

    @model_validator(mode="after")
    def _proposer_matches_origin(self) -> CellWaxProposed:
        """Require a proposer exactly when a bee, not the human, noticed the caution."""
        _check_proposer_matches_origin(self.origin, self.proposer, "CellWaxProposed")
        return self


class CellWaxWritten(WaggleMessage):
    """Deliver a written Cell Wax note (cell.wax_written, an event).

    The full note travels so the Cell's Warden holds it even when offline.
    """

    wax_id: _WaxId = Field(description="The note's id, minted by the Queen.")
    cell_id: CellIdField = Field(description="The Cell marked.")
    severity: WaxSeverity = Field(description="As written; the Queen may downgrade a proposal.")
    text: _WaxText = Field(description="The caution as written.")
    reason: _Reason = Field(description="Why the Queen wrote it.")
    clearance: HoneyClearance = Field(
        description="The label carried into hot state and later Honey."
    )
    expires_at: UtcDatetime | None = Field(description="Expiry, if any.")
    origin: WaxOrigin = Field(description="Who proposed it.")
    proposer: _Proposer | None = Field(
        description="The proposing bee; None exactly when origin is HUMAN."
    )
    decided_by: WaxDecision = Field(description="By rule or by a model episode.")

    @model_validator(mode="after")
    def _proposer_matches_origin(self) -> CellWaxWritten:
        """Require a proposer exactly when a bee, not the human, noticed the caution."""
        _check_proposer_matches_origin(self.origin, self.proposer, "CellWaxWritten")
        return self


class CellWaxCleared(WaggleMessage):
    """Announce a Cell Wax note as cleared (cell.wax_cleared, an event).

    Cleared by the Queen, expired on the sweep, or retired with its destroyed Virtual Cell.
    """

    wax_id: _WaxId = Field(description="The note cleared.")
    cell_id: CellIdField = Field(description="The Cell it marked.")
    cause: WaxClearCause = Field(description="Why it left the written state.")
    reason: _Reason = Field(description="The decision text; expiry names the sweep.")
