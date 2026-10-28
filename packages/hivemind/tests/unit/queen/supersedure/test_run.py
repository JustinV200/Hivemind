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

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from builders.supersedure import (
    FakeCandidate,
    FakeControls,
    FakeSender,
    endpoint,
    make_recorder,
)

from hivemind.common.backup import BackupSource
from hivemind.pheromone import TrailQuery
from hivemind.queen.supersedure import (
    ABORTED_KIND,
    STARTED_KIND,
    SUPERSEDED_KIND,
    AbortCause,
    Handover,
    Rollback,
    SupersedurePlan,
    SupersedurePorts,
    begin,
    finish,
    next_sequence,
)
from waggle.clock import FakeClock


def _source(tmp_path: Path, hive_id: str) -> BackupSource:
    """A one-table SQLite database to back up."""
    db = tmp_path / "hive.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.commit()
    return BackupSource(db=db, hive_id=hive_id)


async def _run(
    tmp_path: Path, candidate: FakeCandidate, grace_s: float
) -> tuple[Handover | Rollback, list[str], FakeSender, SupersedurePorts]:
    """Run one Supersedure over the fakes and return what it did."""
    clock = FakeClock()
    recorder = make_recorder(clock)
    sender = FakeSender(candidate.calls)
    ports = SupersedurePorts(recorder, FakeControls(candidate.calls), candidate, sender)
    plan = SupersedurePlan(
        candidate=endpoint("node_01ARZ3NDEKTSV4RRFFQ69G5FAV", 9001),
        own=endpoint(recorder.node_id, 9000),
        actor="device_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        grace_s=grace_s,
        poll_s=0.0,
        workdir=tmp_path / "work",
    )
    record = await begin(plan, ports)
    outcome = await finish(record, plan, ports, _source(tmp_path, recorder.hive_id))
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
    ("candidate", "grace_s", "cause", "copied"),
    [
        (FakeCandidate([], fail_step="restore"), 60.0, AbortCause.COPY_FAILED, ["copy", "restore"]),
        (
            FakeCandidate([], ready_after=None),
            0.0,
            AbortCause.NOT_READY,
            ["copy", "restore", "start"],
        ),
    ],
)
async def test_a_failed_copy_or_a_late_candidate_is_rolled_back(
    tmp_path: Path, candidate: FakeCandidate, grace_s: float, cause: AbortCause, copied: list[str]
) -> None:
    outcome, calls, sender, ports = await _run(tmp_path, candidate, grace_s)

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
