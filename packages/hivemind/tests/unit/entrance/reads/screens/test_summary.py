"""Test hivemind.entrance.reads.screens.summary: the strip's counters, read from a small Hive.

An idle Hive with one Warden that has not yet reported has no active bee and no active Cell; once
a Heartbeat lists a running sub-bee it has two active bees and one active Cell, and a recent awake
episode makes the Queen an awake bee.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/summary.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.screens import heartbeat, scene
from builders.memory import make_episode
from builders.observation import make_event

from hivemind.entrance.reads.screens import read_summary
from hivemind.forage import ModelSlot
from hivemind.memory import MemoryContext, record_episode
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id
from waggle.messages.supervision import WorkerState


async def test_a_warden_that_has_not_reported_counts_no_active_bee_or_cell() -> None:
    clock = FakeClock()
    hive = scene(clock)

    view = await read_summary(hive.reads, clock.now())

    assert (view.active_bees, view.cells_total, view.cells_active, view.cells_inactive) == (
        0,
        1,
        0,
        1,
    )
    assert (view.cells_real, view.cells_virtual) == (1, 0)
    assert (view.bees_awake, view.bees_autopilot, view.open_alarms) == (0, 0, 0)


async def test_a_heartbeat_an_awake_episode_and_an_alarm_move_the_counters() -> None:
    clock = FakeClock()
    hive = scene(clock)
    hive.board.record(hive.link.warden_id, heartbeat([("", WorkerState.RUNNING)]), clock.now())
    # Seen by the strip as a Warden state: the census reads the board's newest Heartbeat.
    awake = make_episode(clock, principal="queen", slot=ModelSlot.QUEEN, is_autopilot=False)
    await record_episode(awake, MemoryContext(hive.deps.memory, hive.deps.identity, clock))
    payload = {"kind": "WORKER_FAILED", "severity": "WARNING", "task_id": None}
    await hive.record(make_event("alarm.raised", new_alarm_id(clock), clock.now(), payload=payload))

    view = await read_summary(hive.reads, clock.now())

    assert (view.active_wardens, view.active_sub_bees, view.active_bees) == (1, 1, 2)
    assert (view.cells_active, view.cells_inactive) == (1, 0)
    assert (view.bees_awake, view.open_alarms) == (1, 1)
