"""Keep two Queens from ever running: what each node's own trail lets its Queen do at start.

ADR-0065's invariant, as data. On every start the composition root reads the newest Supersedure on
this node's trail (`.record.latest`) and `start_rule` says what follows:

- none, or one this node had nothing to do with, or one it rolled back as the old Queen: RUN;
- this node is the candidate and nothing has ended it: HOLD (Requeening's first half runs, but
  she places nothing and does not settle until `CandidateHold` lifts);
- this node is the old Queen and nothing has ended it: SETTLE_FIRST (she does not start; the human
  settles it after checking the candidate, `settle`);
- this node is the old Queen and it ended in `queen.superseded`: STOOD_DOWN (never again here);
- this node is the candidate and it ended in `queen.supersedure_aborted`: ABORTED_HERE.

`CandidateHold` records `queen.supersedure_ready` once, then holds until her trail has the
acknowledgement, or the grace deadline has passed and the old Queen no longer answers, when it
records her own `queen.superseded` with `acknowledged = false`. An abort recorded on her trail
holds her for good.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Called by the composition root
    (`check_start`, `CandidateHold`) and by `hive supersede --settle` (`settle`); the hold is a
    `hivemind.queen.requeening.RequeeningHold`. Calls into `hivemind.queen.errors`, `.ports`,
    `.record`, `hivemind.common.logging` and waggle only.

Key invariants:
    - No rule returns RUN for an old Queen whose Supersedure has no outcome on her own trail.
    - The hold never lifts for an abort, and lifts on expiry only with the old Queen unreachable.

See Also:
    - docs/adr/0065-supersedure-moving-the-hive-stand.md, "Never two Queens".
"""

from __future__ import annotations

from enum import Enum

from hivemind.common.logging import get_logger
from hivemind.pheromone import PheromoneTrail
from hivemind.queen.errors import SupersedurePendingError
from hivemind.queen.supersedure.ports import OldQueenProbe
from hivemind.queen.supersedure.record import (
    ABORTED_KIND,
    READY_KIND,
    SUPERSEDED_KIND,
    AbortCause,
    Recorder,
    SupersedureRecord,
    latest,
)

log = get_logger(__name__)

__all__ = ["CandidateHold", "StartRule", "check_start", "settle", "start_rule"]


class StartRule(Enum):
    """What a node's Queen may do at start, given its newest Supersedure."""

    RUN = "RUN"
    HOLD = "HOLD"
    SETTLE_FIRST = "SETTLE_FIRST"
    STOOD_DOWN = "STOOD_DOWN"
    ABORTED_HERE = "ABORTED_HERE"


# What the operator reads when a start is refused, by rule.
_WHY = {
    StartRule.SETTLE_FIRST: "this Queen started it and has no outcome; check the candidate, then "
    "run `hive supersede --settle superseded|aborted` (docs/runbooks/supersedure.md)",
    StartRule.STOOD_DOWN: "this Queen was superseded; the Hive Stand is on the new machine now",
    StartRule.ABORTED_HERE: "it was rolled back; this copy of the Hive must never run",
}


def start_rule(record: SupersedureRecord | None, node_id: str) -> StartRule:
    """Decide what this node's Queen may do (module docstring).

    Args:
        record: The newest Supersedure on this node's trail, or None.
        node_id: This node.

    Returns:
        The rule.
    """
    if record is None:
        return StartRule.RUN
    if record.candidate_node_id == node_id:
        if record.outcome is None:
            return StartRule.HOLD
        return StartRule.ABORTED_HERE if record.outcome == ABORTED_KIND else StartRule.RUN
    if record.old_node_id == node_id:
        if record.outcome is None:
            return StartRule.SETTLE_FIRST
        return StartRule.STOOD_DOWN if record.outcome == SUPERSEDED_KIND else StartRule.RUN
    return StartRule.RUN  # A Supersedure between two other nodes, copied in long ago.


async def check_start(trail: PheromoneTrail, node_id: str) -> SupersedureRecord | None:
    """Refuse a start the trail forbids; return the Supersedure to hold for, if any.

    Args:
        trail: This node's trail.
        node_id: This node.

    Returns:
        The Supersedure when the rule is HOLD; None when the Queen simply runs.

    Raises:
        SupersedurePendingError: The rule is SETTLE_FIRST, STOOD_DOWN or ABORTED_HERE.
    """
    record = await latest(trail)
    rule = start_rule(record, node_id)
    if record is None or rule is StartRule.RUN:
        return None
    if rule is StartRule.HOLD:
        return record
    raise SupersedurePendingError(record.started_id, _WHY[rule])


async def settle(
    recorder: Recorder, record: SupersedureRecord, superseded: bool, actor: str
) -> str:
    """Record the human's verdict on an unfinished Supersedure (after a crash).

    Args:
        recorder: This node's trail.
        record: The unfinished Supersedure.
        superseded: True when the candidate has the Hive; False to record it rolled back.
        actor: The human.

    Returns:
        The outcome event's id.
    """
    if superseded:
        return await recorder.record(
            SUPERSEDED_KIND, actor, started_id=record.started_id, acknowledged=False
        )
    return await recorder.record(
        ABORTED_KIND, actor, started_id=record.started_id, cause=AbortCause.SETTLED.value
    )


class CandidateHold:
    """A candidate's `RequeeningHold`: held until acknowledged, or orphaned by a dead old Queen."""

    def __init__(self, record: SupersedureRecord, recorder: Recorder, probe: OldQueenProbe) -> None:
        """Hold for `record` on `recorder`'s trail, asking `probe` once the window is over.

        Args:
            record: The Supersedure this node is the candidate of.
            recorder: The candidate's own trail.
            probe: Whether the old Queen still answers.
        """
        self._record = record
        self._recorder = recorder
        self._probe = probe
        self._ready_noted = False

    async def holds(self) -> bool:
        """Return True while she may not leave REQUEENING (module docstring)."""
        started = self._record.started_id
        if not self._ready_noted:
            # Once per process: the old Queen polls for it; a restart writes it again, harmlessly.
            await self._recorder.record(READY_KIND, self._recorder.hive_id, started_id=started)
            self._ready_noted = True
        now = await latest(self._recorder.trail)
        outcome = now.outcome if now is not None and now.started_id == started else None
        if outcome == SUPERSEDED_KIND:
            return False
        if outcome == ABORTED_KIND or self._recorder.clock.now() < self._record.grace_until:
            return True
        if await self._probe.reachable():
            return True  # The old Queen answers: she decides, and she rolled back or will.
        log.warning("queen.supersedure_orphaned", started_id=started)
        await self._recorder.record(
            SUPERSEDED_KIND, self._recorder.hive_id, started_id=started, acknowledged=False
        )
        return False
