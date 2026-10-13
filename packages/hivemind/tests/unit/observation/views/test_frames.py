"""Check the telemetry frames: a Heartbeat splits into the Warden's sample and one per sub-bee.

A client folds one Heartbeat as its Warden's whole current set, so the Warden's own sample says how
many sub-bee rows follow it (a finished Drone is never sent as DONE, it is just not listed again);
a sub-bee's row carries no count.
"""

from __future__ import annotations

from builders.entrance.screens import heartbeat
from builders.observation import after

from hivemind.observation import telemetry_samples
from waggle.clock import FakeClock
from waggle.ids import new_warden_id
from waggle.messages.supervision import WorkerState

_WARDEN = new_warden_id(FakeClock())


def test_the_wardens_sample_counts_the_sub_bee_rows_that_follow_it() -> None:
    beat = heartbeat([("", WorkerState.SPAWNED), ("", WorkerState.RUNNING)])

    own, *rows = telemetry_samples(_WARDEN, beat, after(1))

    assert own.worker_id is None and own.sub_bees == 2
    assert [row.worker_state for row in rows] == [WorkerState.SPAWNED, WorkerState.RUNNING]
    assert all(row.sub_bees is None for row in rows)


def test_a_heartbeat_without_sub_bees_says_zero() -> None:
    [own] = telemetry_samples(_WARDEN, heartbeat(), after(1))

    assert own.sub_bees == 0
