"""Define what Nectar intake takes and returns: a whole submission, a deposit's source, a result.

Nectar is raw information a bee (a Worker or a Warden, the Hive's agents) or the Hive itself
brings back for the Honey Store, the Hive's knowledge base. Intake accepts it two ways: a whole
`NectarSubmission` built in-process (a verified task's outcome, aged Bee Bread -- the warm memory
tier --, cleared Cell Wax -- the Queen's cautions about one Cell --, the human's proposed note),
or chunk by chunk over Waggle (the bee-to-bee wire protocol) as `NectarDeposit` messages whose
reassembled content `submission_from_deposit` turns into the same submission. `DepositSource` is
what the Queen herself knows about the Warden a chunk arrived from; intake trusts it over
anything the chunk claims. `IntakeResult` is what a caller gets back.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.nectar`.
    Built by the Queen's tick (a `DepositSource` per relaying Warden), by the House Bee and the
    Queen's own in-process deposits (`NectarSubmission`), and read by
    `hivemind.honey_store.nectar.intake.NectarIntake`. Calls into `hivemind.cell`,
    `hivemind.honey_store.models`, `hivemind.manifest` and `waggle` only; no I/O.

Key invariants:
    - `NectarSubmission.tier` and `.from_borrowed_cell` come from the Queen's own record of the
      Cell, never from a sender's claim (docs/waggle/spec.md section 8.7's receiver rule), so
      `submission_from_deposit` takes them from `DepositSource`, never from the deposit.
    - A HANDOFF deposit carries `source_key = handoff:<event id>` (`handoff_source_key`), the same
      key the House Bee gives the same Handoff when it ripens Bee Bread, so the two arrivals dedupe
      into one Nectar row (ADR-0035).

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the intake rules these shapes feed.
    - hivemind.honey_store.nectar.intake for NectarIntake, which consumes every shape here.
    - waggle.messages.honey.exchange for NectarDeposit, the wire chunk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.models import Nectar, NectarOrigin
from hivemind.honey_store.models.nectar import MAX_SOURCE_KEY_CHARS, MIN_NECTAR_BYTES
from hivemind.manifest.schema.honey import MAX_NECTAR_BYTES_CEILING
from waggle.ids import CellId, EventId, IdKind, TaskId, WardenId, WorkerId
from waggle.messages.base import EventIdField, UtcDatetime, id_validator
from waggle.messages.honey import NectarDeposit, NectarKind
from waggle.messages.honey.exchange import (
    MAX_MEDIA_TYPE_CHARS,
    MAX_TITLE_CHARS,
    MIN_MEDIA_TYPE_CHARS,
)
from waggle.messages.honey.hit import MAX_SCOPE_CHARS, SCOPE_PATTERN

HANDOFF_SOURCE_KEY_PREFIX = "handoff:"  # ADR-0035's own spelling of a Handoff's dedupe key.

# The bee a submission came from: a Worker or a Warden, validated exactly as NectarDraft.bee is.
_BeeIdField = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]
# A proposed scope, shaped like HoneyHit.scope; hivemind.honey_store.scope re-checks it stricter.
_ScopeField = Annotated[str, Field(max_length=MAX_SCOPE_CHARS, pattern=SCOPE_PATTERN)]

__all__ = [
    "HANDOFF_SOURCE_KEY_PREFIX",
    "DepositSource",
    "IntakeResult",
    "NectarSubmission",
    "handoff_source_key",
    "submission_from_deposit",
]


class NectarSubmission(BaseModel):
    """One whole Nectar deposit and its provenance, ready for `NectarIntake.submit`.

    Crosses from the Queen's tick, the House Bee and the Queen's own completion path into intake;
    carries the depositor's declared label, never the stored one, which intake computes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: NectarKind = Field(description="What sort of finding this is.")
    origin: NectarOrigin = Field(description="How the deposit reached intake.")
    media_type: str = Field(
        min_length=MIN_MEDIA_TYPE_CHARS,
        max_length=MAX_MEDIA_TYPE_CHARS,
        description="MIME type of the content; ripening decodes by it.",
    )
    title: str = Field(max_length=MAX_TITLE_CHARS, description="A one-line label.")
    content: bytes = Field(
        min_length=MIN_NECTAR_BYTES,
        max_length=MAX_NECTAR_BYTES_CEILING,
        description="The whole raw content; intake refuses it over [honey.store] max_nectar_bytes.",
    )
    task_id: TaskId | None = Field(description="The task it came from, if any.")
    cell_id: CellId = Field(description="The Cell where it was gathered.")
    bee: _BeeIdField | None = Field(
        default=None, description="The Worker or Warden that gathered it; None otherwise."
    )
    observed_at: UtcDatetime = Field(description="When the finding was observed.")
    declared: HoneyClearance | None = Field(
        description="The depositor's own label; None when it declared none. Intake only ever "
        "raises it to the provenance floor, never lowers it."
    )
    from_borrowed_cell: bool = Field(
        description="Whether it was gathered on a Real (borrowed) Cell, from the Queen's record."
    )
    tier: CombShieldLevel = Field(
        description="The Comb Shield tier of the Cell, from the Queen's own record of it, never "
        "from the sender's claim."
    )
    proposed_scope: _ScopeField | None = Field(
        default=None, description="A HUMAN origin's own folder scope; ignored for other origins."
    )
    source_key: str | None = Field(
        default=None,
        max_length=MAX_SOURCE_KEY_CHARS,
        description="An internal dedupe key ('handoff:<event id>', 'wax:<id>', ...), if any.",
    )
    event_id: EventIdField | None = Field(
        default=None,
        description="For a HANDOFF, the memory.checkpoint event it was recorded under.",
    )


@dataclass(frozen=True, slots=True)
class DepositSource:
    """What the Queen knows about the Warden a Nectar chunk arrived from; trusted over the chunk."""

    sender: str  # The envelope sender: the Warden that relayed (or itself made) the deposit.
    cell_id: CellId  # The Cell the sending Warden supervises, from the Queen's own link record.
    from_borrowed_cell: bool  # `Cell.is_borrowed` for that Cell: True for a Real (borrowed) Cell.
    tier: CombShieldLevel  # That Cell's Comb Shield tier, from the Queen's record.


@dataclass(frozen=True, slots=True)
class IntakeResult:
    """What one accepted deposit became: the stored row, whether it was new, whether ephemeral."""

    nectar: Nectar  # The stored (or deduplicated-onto) Nectar row.
    is_new: bool  # False when the deposit deduped onto a row that already existed.
    ephemeral: bool  # True when stored as a Night Veil Cell's side channel, purged at teardown.


def handoff_source_key(event_id: EventId) -> str:
    """Build the dedupe key a Handoff carries, whichever way it reaches intake (ADR-0035).

    Args:
        event_id: The `memory.checkpoint` trail event the Handoff was recorded under.

    Returns:
        `"handoff:<event id>"`.
    """
    return f"{HANDOFF_SOURCE_KEY_PREFIX}{event_id}"


def submission_from_deposit(
    deposit: NectarDeposit, content: bytes, source: DepositSource
) -> NectarSubmission:
    """Build the submission a completed, verified chunked deposit stands for.

    Args:
        deposit: The deposit's final chunk; every chunk of a verified group repeats the same
            metadata, so any one of them describes the whole.
        content: The reassembled, digest-verified content.
        source: What the Queen knows about the Warden it arrived from.

    Returns:
        A BEE-origin submission whose tier and borrowed flag come from `source`, whose declared
        label is the deposit's own, whose bee is the depositing Worker (None for a Warden's own
        deposit), and whose `source_key` is the Handoff's own key for a HANDOFF.
    """
    # Only a HANDOFF carries an event id (NectarDeposit's own validator), so only a Handoff gets
    # the shared dedupe key its Bee Bread copy will carry too.
    source_key = handoff_source_key(deposit.event_id) if deposit.event_id is not None else None
    return NectarSubmission(
        kind=deposit.kind,
        origin=NectarOrigin.BEE,
        media_type=deposit.media_type,
        title=deposit.title,
        content=content,
        task_id=deposit.task_id,
        cell_id=deposit.cell_id,
        bee=deposit.worker_id,
        observed_at=deposit.observed_at,
        declared=HoneyClearance.from_wire(deposit.clearance),
        from_borrowed_cell=source.from_borrowed_cell,
        tier=source.tier,
        source_key=source_key,
        event_id=deposit.event_id,
    )
