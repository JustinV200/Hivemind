"""Hand the Hive to a ready candidate: stand down, acknowledge her, then tell every device.

ADR-0065's handover order. Once the candidate's trail holds `queen.supersedure_ready`, the old
Queen moves SUPERSEDING -> SUPERSEDED and records `queen.superseded` on her own trail, so a restart
of hers finds the outcome and never starts again. She then acknowledges the candidate over the
session, which records the candidate's own `queen.superseded` and lifts her hold. Only then does
the `QueenMoved` go out (the sequence reserved at the start, effective now, both addresses honoured
for the grace window), so no device is ever pointed at a Queen nobody acknowledged. If the
acknowledgement cannot be delivered the move still stands: the old Queen has stood down, and the
candidate lifts her own hold once the window is over and the old Queen no longer answers.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Called by `.run`; `relocation_notice`
    is shared with `.rollback`. Calls into `.ports`, `.record` and waggle only.

Key invariants:
    - SUPERSEDED and its trail record come before the acknowledgement, and both before the notice.
    - `queen.superseded` carries ids, the sequence and booleans only.

See Also:
    - docs/waggle/enrolment.md section 12 for what a device does with the notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from hivemind.queen.supersedure.ports import CandidateLink, Endpoint, NoticeSender, QueenControls
from hivemind.queen.supersedure.record import SUPERSEDED_KIND, Recorder, SupersedureRecord
from waggle.messages.control import QueenMoved

MOVE_REASON = "Supersedure: the Hive Stand moved to a new machine."
ROLLBACK_REASON = "Supersedure rolled back: the Hive Stand stays where it was."

__all__ = [
    "MOVE_REASON",
    "ROLLBACK_REASON",
    "Announcement",
    "Handover",
    "hand_over",
    "relocation_notice",
]


@dataclass(frozen=True, slots=True)
class Announcement:
    """Who a `QueenMoved` points the devices at, and how long both addresses are honoured."""

    to: Endpoint
    grace_s: float


@dataclass(frozen=True, slots=True)
class Handover:
    """What the handover did.

    Attributes:
        acknowledged: The candidate recorded the acknowledgement.
        notices_sent: How many devices the `QueenMoved` reached.
    """

    acknowledged: bool
    notices_sent: int


def relocation_notice(
    sequence: int, announce: Announcement, now: datetime, *, rollback: bool
) -> QueenMoved:
    """Build the `QueenMoved` pointing every device at `announce.to`, effective now.

    Args:
        sequence: Strictly above every notice the Hive sent before.
        announce: The Queen the devices dial from now on, and the window.
        now: The current time.
        rollback: True when the old Queen points the devices back at herself.

    Returns:
        The notice; the device link signs it with the Hive key as it goes.
    """
    to = announce.to
    return QueenMoved(
        sequence=sequence,
        new_address=to.address,
        new_node_id=to.node_id,
        new_node_public_key_hex=to.public_key_hex,
        new_tls_pin_sha256=to.tls_pin,
        effective_at=now,
        grace_until=now + timedelta(seconds=announce.grace_s),
        is_rollback=rollback,
        reason=ROLLBACK_REASON if rollback else MOVE_REASON,
    )


async def hand_over(
    record: SupersedureRecord,
    announce: Announcement,
    recorder: Recorder,
    parts: tuple[QueenControls, CandidateLink, NoticeSender],
) -> Handover:
    """Stand down, acknowledge the candidate, then send the `QueenMoved` (module docstring).

    Args:
        record: The Supersedure, its candidate ready.
        announce: Where the new Queen serves, and the window.
        recorder: The old Queen's trail.
        parts: Her levers, the candidate's link and every device link.

    Returns:
        Whether the acknowledgement landed, and how many devices heard the notice.
    """
    controls, link, sender = parts
    await controls.stand_down()
    await recorder.record(
        SUPERSEDED_KIND,
        recorder.hive_id,
        started_id=record.started_id,
        candidate_node_id=record.candidate_node_id,
        notice_sequence=record.sequence,
        acknowledged=True,
    )
    acknowledged = await link.acknowledge(record.started_id)
    notice = relocation_notice(record.sequence, announce, recorder.clock.now(), rollback=False)
    return Handover(acknowledged=acknowledged, notices_sent=await sender.broadcast(notice))
