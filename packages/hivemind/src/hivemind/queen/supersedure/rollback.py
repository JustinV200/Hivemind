"""Roll a Supersedure back: the old Queen keeps the Hive, and every device is pointed back at her.

ADR-0065. When the copy fails or the candidate is not ready by the grace deadline, the old Queen
records `queen.supersedure_aborted` (with the cause and the sequence of the notice it sends) before
anything else, so a restart of hers finds the outcome and starts normally. She tells the candidate
to discard the copy (best effort: a candidate that is gone cannot hear it, and the runbook says to
settle it by hand before it ever restarts), moves SUPERSEDING -> CLUSTERED and resumes every
provider the freeze clustered, and sends a `QueenMoved` pointing back at herself with a sequence
above the move's. A device that never heard the move keeps its address; one that did comes back.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Called by `.run`. Calls into
    `.handover` (the notice), `.ports`, `.record` and waggle only.

Key invariants:
    - The aborted record comes first, then the discard, the wake and the notice.
    - The rollback notice's sequence is strictly above every notice the Hive sent before.

See Also:
    - docs/runbooks/supersedure.md for settling a candidate that was not reachable.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.queen.supersedure.handover import Announcement, relocation_notice
from hivemind.queen.supersedure.ports import CandidateLink, NoticeSender, QueenControls
from hivemind.queen.supersedure.record import (
    ABORTED_KIND,
    AbortCause,
    Recorder,
    SupersedureRecord,
    next_sequence,
)

__all__ = ["Rollback", "roll_back"]


@dataclass(frozen=True, slots=True)
class Rollback:
    """What the rollback did.

    Attributes:
        cause: Why.
        discarded: The candidate recorded the abort on its own trail.
        notices_sent: How many devices the rollback notice reached.
    """

    cause: AbortCause
    discarded: bool
    notices_sent: int


async def roll_back(
    record: SupersedureRecord,
    cause: AbortCause,
    announce: Announcement,
    recorder: Recorder,
    parts: tuple[QueenControls, CandidateLink | None, NoticeSender],
) -> Rollback:
    """Keep the Hive on this Queen and point every device back at her (module docstring).

    Args:
        record: The Supersedure being rolled back.
        cause: Why.
        announce: This Queen's endpoint, and how long the rollback notice's window lasts.
        recorder: Her trail.
        parts: Her levers, the candidate's link (None if it was never reached) and every device
            link.

    Returns:
        What was done.
    """
    controls, link, sender = parts
    sequence = await next_sequence(recorder.trail)
    await recorder.record(
        ABORTED_KIND,
        recorder.hive_id,
        started_id=record.started_id,
        candidate_node_id=record.candidate_node_id,
        cause=cause.value,
        notice_sequence=sequence,
    )
    discarded = await link.discard(record.started_id) if link is not None else False
    await controls.wake()
    notice = relocation_notice(sequence, announce, recorder.clock.now(), rollback=True)
    return Rollback(cause=cause, discarded=discarded, notices_sent=await sender.broadcast(notice))
