"""Drive one Supersedure from the freeze to its one outcome: superseded or rolled back.

ADR-0065, in two halves so the human's request returns once the move is under way. `begin` freezes
the old Queen (every provider clustered, then SUPERSEDING) and records `queen.supersedure_started`
with a fresh notice sequence and the grace deadline, before a byte is copied. `finish` copies the
stores and starts the candidate's Queen (`.copy`), then asks the candidate every `poll_s` whether
she is ready: the first yes hands over (`.handover`); a failed copy, or no yes by the deadline,
rolls back (`.rollback`). Whatever happens, exactly one outcome lands on the old Queen's trail.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Called by `.door` (the running
    Queen) and by the in-process proof. Calls into `hivemind.queen.errors` and this package's
    `.copy`, `.handover`, `.ports`, `.record` and `.rollback` only.

Key invariants:
    - `queen.supersedure_started` is recorded after the freeze and before the copy.
    - `finish` always ends in `hand_over` or `roll_back`, never neither.

See Also:
    - docs/runbooks/supersedure.md for what the operator sees at each step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from hivemind.common.backup import BackupSource
from hivemind.queen.errors import SupersedureFailedError
from hivemind.queen.supersedure.copy import copy_stores
from hivemind.queen.supersedure.handover import Announcement, Handover, hand_over
from hivemind.queen.supersedure.ports import CandidateLink, Endpoint, NoticeSender, QueenControls
from hivemind.queen.supersedure.record import (
    STARTED_KIND,
    AbortCause,
    Recorder,
    SupersedureRecord,
    next_sequence,
)
from hivemind.queen.supersedure.rollback import Rollback, roll_back

__all__ = ["SupersedurePlan", "SupersedurePorts", "begin", "finish"]


@dataclass(frozen=True, slots=True)
class SupersedurePlan:
    """One Supersedure, as the human asked for it.

    Attributes:
        candidate: Where the new Queen will serve.
        own: This Queen, whom a rollback points the devices back at.
        actor: The human's device, recorded as the actor of the start.
        grace_s: How long the candidate has to be ready; also the notice's window.
        poll_s: How often the old Queen asks.
        workdir: Where the archive is written first.
    """

    candidate: Endpoint
    own: Endpoint
    actor: str
    grace_s: float
    poll_s: float
    workdir: Path


@dataclass(frozen=True, slots=True)
class SupersedurePorts:
    """What the driver acts through (`.ports`)."""

    recorder: Recorder
    controls: QueenControls
    link: CandidateLink
    sender: NoticeSender


async def begin(plan: SupersedurePlan, ports: SupersedurePorts) -> SupersedureRecord:
    """Freeze the Queen and record the start (module docstring).

    Args:
        plan: The Supersedure.
        ports: Its trail, levers, candidate and devices.

    Returns:
        The Supersedure as recorded.
    """
    await ports.controls.freeze()
    recorder = ports.recorder
    sequence = await next_sequence(recorder.trail)
    grace_until = recorder.clock.now() + timedelta(seconds=plan.grace_s)
    started_id = await recorder.record(
        STARTED_KIND,
        plan.actor,
        candidate_node_id=plan.candidate.node_id,
        sequence=sequence,
        grace_until=grace_until.isoformat(),
        old_address=plan.own.address,
    )
    return SupersedureRecord(
        started_id=started_id,
        old_node_id=recorder.node_id,
        candidate_node_id=plan.candidate.node_id,
        sequence=sequence,
        grace_until=grace_until,
        old_address=plan.own.address,
        outcome=None,
    )


async def finish(
    record: SupersedureRecord, plan: SupersedurePlan, ports: SupersedurePorts, source: BackupSource
) -> Handover | Rollback:
    """Copy, wait for the candidate, then hand over or roll back (module docstring).

    Args:
        record: The Supersedure `begin` recorded.
        plan: The Supersedure.
        ports: Its trail, levers, candidate and devices.
        source: What the backup copies.

    Returns:
        The handover, or the rollback with its cause.
    """
    recorder = ports.recorder
    try:
        await copy_stores(source, ports.link, plan.workdir, recorder.clock)
    except SupersedureFailedError:
        return await _roll_back(record, AbortCause.COPY_FAILED, plan, ports)
    while recorder.clock.now() < record.grace_until:
        if await ports.link.is_ready(record.started_id):
            parts = (ports.controls, ports.link, ports.sender)
            announce = Announcement(plan.candidate, plan.grace_s)
            return await hand_over(record, announce, recorder, parts)
        await recorder.clock.sleep(plan.poll_s)
    return await _roll_back(record, AbortCause.NOT_READY, plan, ports)


async def _roll_back(
    record: SupersedureRecord, cause: AbortCause, plan: SupersedurePlan, ports: SupersedurePorts
) -> Rollback:
    """Roll back with this plan's endpoint and window."""
    parts = (ports.controls, ports.link, ports.sender)
    announce = Announcement(plan.own, plan.grace_s)
    return await roll_back(record, cause, announce, ports.recorder, parts)
