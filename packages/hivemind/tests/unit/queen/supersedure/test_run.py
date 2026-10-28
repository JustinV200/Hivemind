"""Test the Supersedure driver: one outcome, in ADR-0065's order, whatever the candidate does.

The old Queen freezes and records the start before a byte is copied. A ready candidate is handed
the Hive: stand down, acknowledge, then announce, with the sequence reserved at the start. A copy
that fails or a candidate not ready by the deadline is rolled back: the abort recorded, the
candidate told to discard, the Queen woken, and a rollback notice with a higher sequence sent.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests hivemind.queen.supersedure.run (and
    .copy, .handover and .rollback through it).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_supersedure.py for two real Queens.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from builders.human import single_task_plan
from builders.queen import make_queen_deps, plan_responder
from builders.supersedure import (
    FakeCandidate,
    FakeControls,
    FakeSender,
    endpoint,
    make_recorder,
)

from hivemind.cell import HoneyClearance
from hivemind.common.backup import BackupSource
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import WardenLink
from hivemind.queen.queen import Queen
from hivemind.queen.state import QueenMode
from hivemind.queen.supersedure import (
    ABORTED_KIND,
    STARTED_KIND,
    SUPERSEDED_KIND,
    AbortCause,
    Handover,
    QueenLevers,
    Rollback,
    SupersedurePlan,
    SupersedurePorts,
    begin,
    finish,
    next_sequence,
)
from hivemind.queen.ticks.results import AttemptBook, retry_task
from waggle.clock import FakeClock


def _source(tmp_path: Path, hive_id: str) -> BackupSource:
    """A one-table SQLite database to back up."""
    db = tmp_path / "hive.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.commit()
    return BackupSource(db=db, hive_id=hive_id)


async def _run(
    tmp_path: Path, candidate: FakeCandidate, grace_s: float, last_step: str | None = None
) -> tuple[Handover | Rollback, list[str], FakeSender, SupersedurePorts]:
    """Run one Supersedure over the fakes on a FakeClock and return what it did.

    With `last_step`, the window is run out (the clock moved past it) once the candidate has
    taken that step, as a candidate that never gets further would see it.
    """
    clock = FakeClock()
    recorder = make_recorder(clock)
    sender = FakeSender(candidate.calls)
    ports = SupersedurePorts(recorder, FakeControls(candidate.calls), candidate, sender)
    plan = SupersedurePlan(
        candidate=endpoint("node_01ARZ3NDEKTSV4RRFFQ69G5FAV", 9001),
        own=endpoint(recorder.node_id, 9000),
        actor="device_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        grace_s=grace_s,
        # Asked again at once when the window never runs out; otherwise each ask waits on the
        # clock, so the loop yields and the test can move it past the window.
        poll_s=0.0 if last_step is None else 1.0,
        workdir=tmp_path / "work",
    )
    record = await begin(plan, ports)
    finishing = asyncio.ensure_future(
        finish(record, plan, ports, _source(tmp_path, recorder.hive_id))
    )
    if last_step is not None:
        await asyncio.wait_for(candidate.taken(last_step), 5.0)
        clock.advance(grace_s)
    outcome = await asyncio.wait_for(finishing, 5.0)
    return outcome, candidate.calls, sender, ports


async def test_a_ready_candidate_is_handed_the_hive_in_order(tmp_path: Path) -> None:
    outcome, calls, sender, ports = await _run(tmp_path, FakeCandidate([], ready_after=1), 60.0)

    assert isinstance(outcome, Handover) and outcome.acknowledged
    assert calls == [
        "freeze",
        "copy",
        "restore",
        "start",
        "is_ready",
        "is_ready",
        "stand_down",
        "acknowledge",
        "broadcast",
    ]
    trail = ports.recorder.trail
    [started] = await trail.query(TrailQuery(kind=STARTED_KIND))
    [superseded] = await trail.query(TrailQuery(kind=SUPERSEDED_KIND))
    assert superseded.payload["started_id"] == started.id
    [notice] = sender.notices
    assert notice.sequence == started.payload["sequence"] and not notice.is_rollback
    assert not (tmp_path / "work" / "supersedure.hive.tgz").exists()


@pytest.mark.parametrize(
    ("candidate", "cause", "copied"),
    [
        (FakeCandidate([], fail_step="restore"), AbortCause.COPY_FAILED, ["copy", "restore"]),
        (FakeCandidate([], ready_after=None), AbortCause.NOT_READY, ["copy", "restore", "start"]),
        # A restore that never returns is cut off by the window, never waited on for ever.
        (FakeCandidate([], stalls=True), AbortCause.NOT_READY, ["copy", "restore"]),
    ],
)
async def test_a_failed_copy_or_a_late_candidate_is_rolled_back(
    tmp_path: Path, candidate: FakeCandidate, cause: AbortCause, copied: list[str]
) -> None:
    # The window runs out once the candidate has gone as far as it will (a failed copy ends first).
    outcome, calls, sender, ports = await _run(tmp_path, candidate, 60.0, copied[-1])
    # It asks as often as the window allows; the order of everything else is what matters.
    calls = [call for call in calls if call != "is_ready"]

    assert isinstance(outcome, Rollback) and outcome.cause is cause
    assert calls == ["freeze", *copied, "discard", "wake", "broadcast"]
    trail = ports.recorder.trail
    [started] = await trail.query(TrailQuery(kind=STARTED_KIND))
    [aborted] = await trail.query(TrailQuery(kind=ABORTED_KIND))
    assert aborted.payload["started_id"] == started.id
    assert aborted.payload["cause"] == cause.value
    [notice] = sender.notices
    assert notice.is_rollback and notice.sequence > started.payload["sequence"]  # type: ignore[operator]
    assert await next_sequence(trail) > notice.sequence
    assert not await trail.query(TrailQuery(kind=SUPERSEDED_KIND))


async def test_a_rollback_wakes_only_what_the_freeze_clustered() -> None:
    """A provider an outage clustered first stays clustered for the health probe to resume."""
    deps, _link, _end = make_queen_deps()
    state = deps.cluster_state
    state.mark_clustered("down")  # A real outage, before the operator started the move.
    levers = QueenLevers(deps, tuple)

    await levers.freeze()
    frozen = state.clustered_providers
    await levers.wake()

    assert {"fake", "down"} <= frozen
    assert state.clustered_providers == {"down"} and state.mode is QueenMode.CLUSTERED


async def test_a_retry_heard_while_superseding_is_resent_after_a_rollback() -> None:
    """ADR-0065: a held retry is never a lost task; the wake resends it as the attempt it took."""
    deps, link, warden_end = make_queen_deps(
        fake_provider=FakeLLMProvider(responder=plan_responder(single_task_plan))
    )
    queen = Queen(deps)
    await queen.attach_warden(link)
    task_id = await queen.submit_goal("Do a thing.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    attached: list[WardenLink] = []  # Its Warden is away for the freeze, so its task stays RUNNING.
    levers = QueenLevers(deps, lambda: tuple(attached))
    await levers.freeze()
    attached.append(link)  # Back mid-move, with a failed attempt the Queen retries.

    await retry_task(deps, attached, task_id, AttemptBook())  # Held: nothing is sent.
    await levers.wake()

    # Bounded: a retry never resent sends nothing, and the pump would wait for it for good.
    await asyncio.wait_for(warden_end.pump_until(lambda: len(warden_end.assignments) >= 2), 5.0)
    assert [a.attempt for a in warden_end.assignments] == [1, 2]
