"""Test "never two Queens": what each node's trail lets its Queen do, and the candidate's hold.

ADR-0065. The old Queen never starts while her Supersedure has no outcome on her own trail, nor
ever again once superseded; a rolled-back candidate never runs; a candidate with no outcome is
held. The hold lifts on the acknowledgement, or after the grace deadline only when the old Queen
no longer answers, and never for an abort.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests hivemind.queen.supersedure.gate.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0065-supersedure-moving-the-hive-stand.md, "Never two Queens".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from builders.supersedure import FakeOldQueen, make_recorder

from hivemind.pheromone import TrailQuery
from hivemind.queen.errors import SupersedurePendingError
from hivemind.queen.supersedure import (
    ABORTED_KIND,
    READY_KIND,
    STARTED_KIND,
    SUPERSEDED_KIND,
    CandidateHold,
    Recorder,
    StartRule,
    SupersedureRecord,
    check_start,
    latest,
    settle,
    start_rule,
)
from waggle.clock import FakeClock

_OLD, _NEW, _OTHER = "node_old", "node_new", "node_other"
_AT = datetime(2026, 10, 27, tzinfo=UTC)
_DEVICE = "device_01ARZ3NDEKTSV4RRFFQ69G5FAV"  # The human's device.


def _record(outcome: str | None) -> SupersedureRecord:
    """A Supersedure from _OLD to _NEW, ended by `outcome`."""
    return SupersedureRecord("evt_1", _OLD, _NEW, 1, _AT, "ws://127.0.0.1:9000", outcome)


@pytest.mark.parametrize(
    ("record", "node", "rule"),
    [
        (None, _OLD, StartRule.RUN),
        (_record(None), _OLD, StartRule.SETTLE_FIRST),
        (_record(SUPERSEDED_KIND), _OLD, StartRule.STOOD_DOWN),
        (_record(ABORTED_KIND), _OLD, StartRule.RUN),
        (_record(None), _NEW, StartRule.HOLD),
        (_record(SUPERSEDED_KIND), _NEW, StartRule.RUN),
        (_record(ABORTED_KIND), _NEW, StartRule.ABORTED_HERE),
        (_record(None), _OTHER, StartRule.RUN),
    ],
)
def test_each_node_obeys_its_own_trail(
    record: SupersedureRecord | None, node: str, rule: StartRule
) -> None:
    assert start_rule(record, node) is rule


async def _started(recorder: Recorder, candidate: str, grace_s: float) -> SupersedureRecord:
    """Record a start naming `candidate`, as the copied trail carries it, and read it back."""
    until = recorder.clock.now() + timedelta(seconds=grace_s)
    await recorder.record(
        STARTED_KIND,
        _DEVICE,
        candidate_node_id=candidate,
        sequence=1,
        grace_until=until.isoformat(),
        old_address="ws://127.0.0.1:9000",
    )
    found = await latest(recorder.trail)
    assert found is not None
    return found


@pytest.mark.parametrize(
    ("outcome", "past_grace", "answers", "holds"),
    [
        (SUPERSEDED_KIND, False, True, False),  # Acknowledged: lifts at once.
        (None, False, False, True),  # Inside the window: the old Queen decides.
        (None, True, True, True),  # Past it, but she answers: she rolled back or will.
        (None, True, False, False),  # Past it and gone: the candidate takes the Hive.
        (ABORTED_KIND, True, False, True),  # Rolled back: never lifts.
    ],
)
async def test_the_candidates_hold(
    outcome: str | None, past_grace: bool, answers: bool, holds: bool
) -> None:
    clock = FakeClock()
    recorder = make_recorder(clock)
    record = await _started(recorder, recorder.node_id, 60.0)
    if outcome is not None:
        await recorder.record(outcome, recorder.hive_id, started_id=record.started_id)
    hold = CandidateHold(record, recorder, FakeOldQueen(answers))
    if past_grace:
        clock.advance(61.0)

    assert await hold.holds() is holds
    assert await hold.holds() is holds
    assert len(await recorder.trail.query(TrailQuery(kind=READY_KIND))) == 1
    orphaned = outcome is None and not holds
    superseded = await recorder.trail.query(TrailQuery(kind=SUPERSEDED_KIND))
    assert len(superseded) == (1 if orphaned or outcome == SUPERSEDED_KIND else 0)


@pytest.mark.parametrize(("superseded", "starts"), [(False, True), (True, False)])
async def test_an_unfinished_old_queen_starts_only_once_the_human_settles_it(
    superseded: bool, starts: bool
) -> None:
    recorder = make_recorder(FakeClock())
    record = await _started(recorder, _NEW, 60.0)
    with pytest.raises(SupersedurePendingError):
        await check_start(recorder.trail, recorder.node_id)

    await settle(recorder, record, superseded, _DEVICE)

    if starts:
        assert await check_start(recorder.trail, recorder.node_id) is None
    else:
        with pytest.raises(SupersedurePendingError):
            await check_start(recorder.trail, recorder.node_id)
