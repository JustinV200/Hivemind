"""Tests for hivemind.wardens.offline.diagnose.drone: sending a Drone, reading its end, retiring it.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/diagnose/drone.py (codingrules section 3), driven through a
    real Warden on a device's deps that holds ceilings and a model server (builders.diagnose), with
    its link left up so that only the calls under test move the search. A Drone is sent only on a
    model this Warden serves itself and a slot the Queen's ceilings leave, it takes no slot of the
    Queen's grant, and it ends by a claim, by an Alarm or by being retired elsewhere; whichever it
    was, `collect` reads it, retires the row once and clears the search.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.diagnose.drone for the module under test.
    - builders.diagnose for the scene and the Drone scripts these tests run.
"""

from __future__ import annotations

from builders.diagnose import hangs, make_nuc, raises, says, start_nuc_scene
from builders.quarantine import settle

from hivemind.wardens import Warden
from hivemind.wardens.offline import RemoteDeps, remote_of
from hivemind.wardens.offline.diagnose import (
    Finding,
    Investigation,
    Start,
    collect,
    has_ended,
    start_drone,
    stop_drone,
)
from hivemind.wardens.ticks.alarms import retire_sub_bee
from hivemind.workers.state import WorkerState
from waggle.messages.task import TaskAssign

_CAUSE = "the resolver file names a host that is gone"
_CLOSING = f"CAUSE: {_CAUSE}\nI removed the line."
_CRASH = "The Drone crashed."  # What the scripted Drone raises with; must never be kept.


def _remote(warden: Warden) -> RemoteDeps:
    """The Warden's link facts, which a Warden that dials the Queen always holds."""
    remote = remote_of(warden)
    assert remote is not None
    return remote


async def test_a_drone_is_sent_on_the_local_model_and_takes_no_slot_of_the_queens() -> None:
    sent: list[TaskAssign] = []
    scene = await start_nuc_scene(hangs(sent))
    search = Investigation(cut_since=scene.clock.now())

    started = await start_drone(scene.warden, search, _remote(scene.warden))

    assert started is Start.STARTED
    [row] = scene.warden.sub_bees
    assert row.local is not None and search.run is row.local
    assert search.worker_id == row.worker_id and search.attempts == 1
    assert row.binding == "worker"  # The key whose whole fallback chain runs on this machine.
    assert row.worker_id in scene.warden._sub_bee_iters  # Its frames are read like any bee's.
    assert scene.warden._sub_bee_slots.in_use == 0
    await settle(lambda: len(sent) == 1)
    assert "Queen at hive.example." in sent[0].objective and sent[0].attempt == 1
    await stop_drone(scene.warden, search)
    await scene.stop()


async def test_each_drone_of_a_search_is_counted_as_the_next_attempt() -> None:
    sent: list[TaskAssign] = []
    scene = await start_nuc_scene(hangs(sent))
    search = Investigation(cut_since=scene.clock.now())
    remote = _remote(scene.warden)

    assert await start_drone(scene.warden, search, remote) is Start.STARTED
    await settle(lambda: len(sent) == 1)  # The first is working before it is retired.
    await stop_drone(scene.warden, search)  # As a search does before it sends the next.
    assert await start_drone(scene.warden, search, remote) is Start.STARTED

    assert search.attempts == 2
    await settle(lambda: len(sent) == 2)
    assert [task.attempt for task in sent] == [1, 2]
    await stop_drone(scene.warden, search)
    await scene.stop()


async def test_a_warden_with_no_model_of_its_own_is_not_eligible_and_sends_nothing() -> None:
    scene = await start_nuc_scene(hangs(), prepare=lambda warden: None)
    search = Investigation(cut_since=scene.clock.now())

    started = await start_drone(scene.warden, search, _remote(scene.warden))

    assert started is Start.NOT_ELIGIBLE
    assert scene.warden.sub_bees == () and search.attempts == 0 and search.run is None
    await scene.stop()


async def test_a_warden_the_queen_has_set_no_ceilings_for_is_not_eligible() -> None:
    scene = await start_nuc_scene(hangs())
    scene.warden._ceilings = None  # Its server runs, but the Queen has not yet bounded the Cell.
    search = Investigation(cut_since=scene.clock.now())

    assert await start_drone(scene.warden, search, _remote(scene.warden)) is Start.NOT_ELIGIBLE
    assert scene.warden.sub_bees == () and search.attempts == 0
    await scene.stop()


async def test_a_cell_already_at_its_ceiling_of_sub_bees_has_no_slot_for_a_drone() -> None:
    scene = await start_nuc_scene(hangs(), assign=True, prepare=lambda warden: make_nuc(warden, 1))
    search = Investigation(cut_since=scene.clock.now())

    started = await start_drone(scene.warden, search, _remote(scene.warden))

    assert started is Start.NO_SLOT  # Ask again when the task ends; this is not a refusal.
    assert len(scene.rows()) == 1 and search.attempts == 0 and search.run is None
    await scene.stop()


async def test_a_drone_that_claimed_is_collected_as_what_it_said_and_retired_once() -> None:
    scene = await start_nuc_scene(says(_CLOSING))
    search = Investigation(cut_since=scene.clock.now())
    assert await start_drone(scene.warden, search, _remote(scene.warden)) is Start.STARTED

    await settle(lambda: has_ended(scene.warden, search))
    finding = await collect(scene.warden, search)

    assert finding == Finding(cause=_CAUSE, text=_CLOSING)
    assert scene.warden.sub_bees == ()  # Retired: the row, its iterator and its link are gone.
    assert search.run is None and search.worker_id is None
    assert not has_ended(scene.warden, search)
    await scene.stop()


async def test_a_drone_that_raised_an_alarm_is_a_finding_of_no_cause_without_its_words() -> None:
    scene = await start_nuc_scene(raises())
    search = Investigation(cut_since=scene.clock.now())
    assert await start_drone(scene.warden, search, _remote(scene.warden)) is Start.STARTED

    await settle(lambda: has_ended(scene.warden, search))
    finding = await collect(scene.warden, search)

    assert finding.cause is None and "stopped with the Alarm" in finding.text
    assert _CRASH not in finding.text  # Only the kind: an Alarm's detail is text a bee wrote.
    assert scene.queen.alarms == []  # The Alarm never left the Warden.
    assert scene.warden.sub_bees == ()
    await scene.stop()


async def test_a_drone_retired_by_another_path_has_ended_and_said_nothing() -> None:
    scene = await start_nuc_scene(hangs())
    search = Investigation(cut_since=scene.clock.now())
    assert await start_drone(scene.warden, search, _remote(scene.warden)) is Start.STARTED
    [row] = scene.warden.sub_bees
    assert not has_ended(scene.warden, search)  # Working, and nothing has been said.

    await retire_sub_bee(scene.warden, row)  # A quarantine, a handoff or a lost lease does this.

    assert has_ended(scene.warden, search)
    assert await collect(scene.warden, search) == Finding(None, "")
    assert search.run is None
    await scene.stop()


async def test_stopping_a_working_drone_retires_it_and_forgets_it_even_if_asked_twice() -> None:
    scene = await start_nuc_scene(hangs())
    search = Investigation(cut_since=scene.clock.now())
    assert await start_drone(scene.warden, search, _remote(scene.warden)) is Start.STARTED
    [row] = scene.warden.sub_bees

    await stop_drone(scene.warden, search)
    await stop_drone(scene.warden, search)  # Nothing left to retire; not an error.

    assert row.runtime.state is not WorkerState.RUNNING
    assert scene.warden.sub_bees == () and search.run is None and search.worker_id is None
    await scene.stop()


async def test_a_search_with_no_drone_in_flight_has_not_ended() -> None:
    scene = await start_nuc_scene(hangs())

    assert not has_ended(scene.warden, Investigation(cut_since=scene.clock.now()))
    await scene.stop()
