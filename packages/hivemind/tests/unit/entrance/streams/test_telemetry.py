"""Test hivemind.entrance.streams.telemetry: the Heartbeats the Queen hands on, kept and fanned out.

The board keeps each Warden's newest Heartbeat (a later one replaces the earlier) as a read-only
snapshot, offers every Heartbeat to every subscriber, and lets one that falls past its backlog go
with FELL_BEHIND without holding up the others or the Queen's tick that recorded it.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/telemetry.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.supervision import make_telemetry

from hivemind.entrance.streams import CloseReason, StreamClosedError, TelemetryBoard
from waggle.clock import FakeClock
from waggle.ids import new_warden_id
from waggle.messages.supervision import Heartbeat, WardenState


def _heartbeat(state: WardenState) -> Heartbeat:
    """A Warden's Heartbeat reporting ``state`` and no sub-bees."""
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=state,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


def test_the_newest_heartbeat_per_warden_is_kept_as_a_snapshot() -> None:
    clock, board = FakeClock(), TelemetryBoard()
    first, second = new_warden_id(clock), new_warden_id(clock)
    board.record(first, _heartbeat(WardenState.STARTING), clock.now())
    board.record(second, _heartbeat(WardenState.WATCH), clock.now())
    snapshot = board.latest()
    board.record(first, _heartbeat(WardenState.ACTIVE), clock.now())

    latest = board.latest()

    assert snapshot[first].heartbeat.warden_state is WardenState.STARTING
    assert latest[first].heartbeat.warden_state is WardenState.ACTIVE
    assert latest[second].heartbeat.warden_state is WardenState.WATCH
    with pytest.raises(TypeError):
        latest[first] = latest[second]  # type: ignore[index]


async def test_a_subscriber_past_its_backlog_goes_and_the_others_still_hear() -> None:
    clock, board = FakeClock(), TelemetryBoard()
    warden = new_warden_id(clock)
    slow, keen = board.subscribe("slow", backlog=1), board.subscribe("keen", backlog=4)

    board.record(warden, _heartbeat(WardenState.ACTIVE), clock.now())
    board.record(warden, _heartbeat(WardenState.WATCH), clock.now())
    heard = await keen.next_batch()

    assert slow.closed is CloseReason.FELL_BEHIND
    with pytest.raises(StreamClosedError):
        await slow.next_batch()
    assert [sample.heartbeat.warden_state for sample in heard] == [
        WardenState.ACTIVE,
        WardenState.WATCH,
    ]
    assert board.subscribers == 1
