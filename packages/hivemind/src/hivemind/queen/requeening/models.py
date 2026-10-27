"""Define the Queen's own durable records for Requeening: her snapshot and what she handled.

Requeening (roadmap 13.1, ADR-0062) restores a Queen whose process died. Most of what she needs
comes back from stores that were durable all along (the Brood Chamber, the Forage ledger, backend
labels); these models are the two pieces nothing else keeps. `QueenSnapshot` is her own periodic
Handoff (roadmap 13.2): when it was taken, the trail instant it folded up to, the Hive Stand leases
open at that instant and the Wardens then attached, so recovery folds the trail only from the
cursor on. `HandledEnvelope` is one Alarm or question she handled from a Warden, noted when she
handled it (never on receipt), so a frame the Warden sends again after her restart is dropped as
the replay it is; a question's row also keeps the wire ids its answer must be forwarded with
(`QuestionWiring`). `OpenLease` is one lease in the snapshot's fold.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. Written and read through `hivemind.queen.requeening.store`; built by
    `.snapshot` (QueenSnapshot, OpenLease) and `.handled` (HandledEnvelope, QuestionWiring).
    Calls into pydantic and waggle only.

Key invariants:
    - Every model is frozen and forbids extra fields: a row read back is exactly what was written.
    - Nothing here carries content: ids, instants and counts only, never a question's text or an
      Alarm's detail (codingrules section 12), so nothing of a Night Veil Cell rides in it beyond
      envelope ids, which expire with the replay window (ADR-0062).
    - A snapshot's `cursor` is never later than its `taken_at`.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for what each record is for.
    - hivemind.queen.requeening.store for where they are kept.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waggle.messages.base import UtcDatetime

MAX_ID_CHARS = 64  # Every id here is a prefixed ULID or a short name, far under this.
MAX_OPEN_LEASES = 64  # The Hive Stand holds one lease at a time; this bounds a corrupt fold.
MAX_SNAPSHOT_WARDENS = 4_096  # Far above any Hive's attached Wardens; bounds the row's size.

__all__ = [
    "MAX_OPEN_LEASES",
    "MAX_SNAPSHOT_WARDENS",
    "HandledEnvelope",
    "OpenLease",
    "QueenSnapshot",
    "QuestionWiring",
]

_Id = Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARS)]
_FROZEN = ConfigDict(frozen=True, extra="forbid")


class OpenLease(BaseModel):
    """One Hive Stand lease the trail shows opened (`cell.leased`) and not yet released."""

    model_config = _FROZEN

    lease_id: _Id = Field(description="The lease's own id, as `cell.leased` named it.")
    cell_id: _Id = Field(description="The Cell it was taken on: the Hive Stand's own Cell.")
    holder: _Id = Field(description="The Warden that held it, which died with its process.")
    task_id: _Id | None = Field(description="The task it was taken for, when it named one.")
    leased_at: UtcDatetime = Field(description="When `cell.leased` was recorded.")


class QueenSnapshot(BaseModel):
    """The Queen's own periodic Handoff: where the trail fold stood, and what was open then."""

    model_config = _FROZEN

    taken_at: UtcDatetime = Field(description="When this snapshot was written.")
    cursor: UtcDatetime = Field(
        description="Every lease event recorded before this instant is folded in; recovery reads "
        "the trail from here on (inclusive, so an event at the cursor itself is read twice, and "
        "folding is idempotent)."
    )
    open_leases: tuple[OpenLease, ...] = Field(
        max_length=MAX_OPEN_LEASES, description="Hive Stand leases open at `cursor`."
    )
    wardens: tuple[_Id, ...] = Field(
        max_length=MAX_SNAPSHOT_WARDENS, description="The Wardens attached when it was taken."
    )

    @model_validator(mode="after")
    def _cursor_not_after_taken(self) -> Self:
        """Refuse a cursor later than the snapshot itself: it would skip events not yet folded."""
        if self.cursor > self.taken_at:
            raise ValueError(f"cursor {self.cursor} is later than taken_at {self.taken_at}.")
        return self


class QuestionWiring(BaseModel):
    """What forwarding an answer to a question needs: the ids the Queen otherwise keeps in memory.

    The Brood Chamber mints its own question id, the asking Warden matches an answer by the wire
    id, and the answer's envelope must name the question's envelope (a reply's `correlation_id`).
    """

    model_config = _FROZEN

    chamber_question_id: _Id = Field(description="The Brood Chamber's own id for the question.")
    wire_question_id: _Id = Field(description="The id the asking Warden matches the answer by.")
    task_id: _Id = Field(description="The task the question blocks.")


class HandledEnvelope(BaseModel):
    """One Alarm or question the Queen handled from a Warden, keyed by sender and envelope id."""

    model_config = _FROZEN

    principal: _Id = Field(description="The Warden whose link it arrived on.")
    envelope_id: _Id = Field(description="The envelope's own id; a resend keeps it.")
    handled_at: UtcDatetime = Field(description="When she finished handling it.")
    question: QuestionWiring | None = Field(
        default=None, description="Set for a question she asked the human; None otherwise."
    )
