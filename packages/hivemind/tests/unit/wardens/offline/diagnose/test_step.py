"""Tests for hivemind.wardens.offline.diagnose.step: a cut-off Nuc searching for why its link went.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/diagnose/step.py (codingrules section 3), driven through a
    real Warden on a device's deps that holds ceilings and a model server (builders.diagnose). A
    Nuc whose link goes sends one Drone from its own pool; the search ends FIXED when the link is
    back and the Drone named a cause, DIAGNOSED when a cause is known and the link is still down,
    NO_CAUSE otherwise; it records `warden.diagnosed`, deposits the findings as Nectar, never sends
    the Queen a task, a result or a child she has never heard of, and never outlasts its budget. A
    Warden with no model of its own sends nothing.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.diagnose.step for the module under test.
    - builders.diagnose for the scene and the Drone scripts these tests run.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from builders.diagnose import (
    BUDGET,
    diagnosed,
    drones,
    hangs,
    in_turn,
    make_nuc,
    nectar_text,
    raises,
    says,
    says_when,
    start_nuc_scene,
)
from builders.offline_warden import WARDEN_BEAT_S, OfflineScene
from builders.quarantine import drain, settle

from hivemind.wardens.errors import BindingRefusedError
from hivemind.wardens.offline import ReconnectBudget
from hivemind.wardens.offline.diagnose import LINGER_S, VERIFY_S, Phase
from hivemind.wardens.state import WardenState
from waggle.messages.task import TaskAssign

_CAUSE = "CAUSE: a stale entry in the resolver file\nI removed it and the name resolves again."


def _phase(scene: OfflineScene) -> Phase | None:
    """The search's phase, read through a call so mypy keeps no narrowing of the attribute."""
    search = scene.outage.investigation
    return None if search is None else search.phase


async def _cut(scene: OfflineScene) -> None:
    """Take the link down and wait for the Warden to notice and send its Drone."""
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)


async def _past(scene: OfflineScene, seconds: float) -> None:
    """Let `seconds` of the Warden's clock pass, and wake its tick to look."""
    scene.clock.advance(seconds)
    scene.outage.nudge()


async def test_a_drone_that_names_a_cause_and_a_link_that_returns_is_fixed() -> None:
    scene = await start_nuc_scene(says(_CAUSE))
    await _cut(scene)
    await settle(lambda: _phase(scene) is Phase.VERIFYING)

    scene.restore()
    await settle(lambda: scene.outage.investigation is None)

    [event] = await diagnosed(scene)
    assert event.payload["outcome"] == "FIXED"
    assert event.payload["reason"] == "link_back"
    assert event.payload["attempts"] == 1
    assert event.payload["deposited"] is True
    text = await nectar_text(scene)
    assert "FIXED" in text and "stale entry in the resolver file" in text
    await scene.stop()


async def test_a_cause_the_link_did_not_follow_is_diagnosed_once_the_wait_is_over() -> None:
    scene = await start_nuc_scene(says(_CAUSE))
    await _cut(scene)
    await settle(lambda: _phase(scene) is Phase.VERIFYING)

    await _past(scene, VERIFY_S + 1.0)
    await settle(lambda: _phase(scene) is Phase.OVER)

    [event] = await diagnosed(scene)
    assert event.payload["outcome"] == "DIAGNOSED"
    assert event.payload["reason"] == "cause_known"
    assert "DIAGNOSED" in await nectar_text(scene)
    assert scene.warden.state is WardenState.OFFLINE  # The search changes nothing about the link.
    await scene.stop()


async def test_a_drone_that_found_nothing_is_followed_by_one_told_what_it_said() -> None:
    sent: list[TaskAssign] = []
    first = says("CAUSE: none found\nThe name resolves and the route is there.", sent)
    scene = await start_nuc_scene(in_turn(first, says("CAUSE: none found", sent)))
    await _cut(scene)

    await settle(lambda: len(sent) == 2 and _phase(scene) is Phase.OVER)

    assert "An earlier attempt reported" in sent[1].objective
    assert "The name resolves and the route is there." in sent[1].objective
    assert "earlier attempt" not in sent[0].objective
    [event] = await diagnosed(scene)
    assert (event.payload["outcome"], event.payload["attempts"]) == ("NO_CAUSE", 2)
    assert event.payload["reason"] == "no_cause"
    await scene.stop()


async def test_a_drone_that_crashes_is_a_finding_of_no_cause_and_the_alarm_stays_here() -> None:
    sent: list[TaskAssign] = []
    scene = await start_nuc_scene(in_turn(raises(sent), says(_CAUSE, sent)))
    await _cut(scene)

    await settle(lambda: len(sent) == 2 and _phase(scene) is Phase.VERIFYING)

    assert "stopped with the Alarm" in sent[1].objective
    assert scene.queen.alarms == [] and scene.queen.results == []
    await scene.stop()


async def test_a_drone_still_working_when_the_budget_runs_out_is_stopped() -> None:
    sent: list[TaskAssign] = []
    scene = await start_nuc_scene(hangs(sent), budget=ReconnectBudget(2, 60.0))
    await _cut(scene)
    await settle(lambda: len(drones(scene)) == 1)

    await _past(scene, 61.0)
    await settle(lambda: _phase(scene) is Phase.OVER)

    assert drones(scene) == [] and len(sent) == 1
    [event] = await diagnosed(scene)
    assert (event.payload["outcome"], event.payload["reason"]) == ("NO_CAUSE", "no_cause")
    spent = event.payload["spent_s"]
    assert isinstance(spent, float) and spent >= 60.0
    await scene.stop()


async def test_a_link_that_returns_alone_with_no_cause_named_credits_no_drone() -> None:
    scene = await start_nuc_scene(hangs())
    await _cut(scene)
    await settle(lambda: len(drones(scene)) == 1)

    scene.restore()
    await settle(lambda: scene.warden.state is not WardenState.OFFLINE)
    await _past(scene, LINGER_S + 1.0)  # The Drone has had its moment to write up, and has not.
    await settle(lambda: scene.outage.investigation is None)

    [event] = await diagnosed(scene)
    assert (event.payload["outcome"], event.payload["reason"]) == ("NO_CAUSE", "link_returned")
    assert drones(scene) == []
    await scene.stop()


async def test_a_drone_that_fixed_the_link_may_write_up_after_it_is_back() -> None:
    written = asyncio.Event()
    scene = await start_nuc_scene(says_when(written, _CAUSE))
    await _cut(scene)
    await settle(lambda: len(drones(scene)) == 1)

    scene.restore()  # The link is back while the Drone is still writing up what it did.
    await settle(lambda: scene.warden.state is not WardenState.OFFLINE)
    assert _phase(scene) is Phase.RUNNING
    written.set()
    await settle(lambda: scene.outage.investigation is None)

    [event] = await diagnosed(scene)
    assert (event.payload["outcome"], event.payload["reason"]) == ("FIXED", "link_back")
    await scene.stop()


async def test_a_warden_with_no_model_of_its_own_sends_no_drone_and_records_nothing() -> None:
    scene = await start_nuc_scene(says(_CAUSE), prepare=lambda warden: None)
    await _cut(scene)
    await settle(lambda: _phase(scene) is Phase.OVER)

    await _past(scene, BUDGET.wall_clock_s + 1.0)
    scene.restore()
    await settle(lambda: scene.outage.investigation is None)

    assert drones(scene) == [] and await diagnosed(scene) == []
    assert scene.queen.nectar == []
    await scene.stop()


async def test_the_queen_is_told_of_no_task_result_alarm_or_child_of_the_drones() -> None:
    scene = await start_nuc_scene(in_turn(raises(), hangs()))
    await _cut(scene)
    await settle(lambda: len(drones(scene)) == 1 and scene.queen is not None)
    await settle(lambda: drones(scene) != [] and drones(scene)[0].local is not None)

    scene.clock.advance(WARDEN_BEAT_S)  # The Warden's own Heartbeat, with the children it lists.
    await scene.queen.wait_for_heartbeat()

    assert scene.queen.results == [] and scene.queen.alarms == []
    assert scene.queen.heartbeats[-1].children == ()  # The second Drone is working; none listed.
    await scene.stop()


async def test_a_cell_at_its_ceiling_makes_the_search_wait_and_end_with_no_drone_sent() -> None:
    # The Queen's task runs on the one place the ceiling allows; a Drone takes no one's slot.
    scene = await start_nuc_scene(
        hangs(),
        assign=True,
        budget=ReconnectBudget(2, 60.0),
        prepare=lambda warden: make_nuc(warden, cap=1),
    )
    await _cut(scene)
    await drain()  # Let the tick look; there is room for nothing.
    assert len(scene.rows()) == 1 and drones(scene) == []

    await _past(scene, 61.0)
    await settle(lambda: _phase(scene) is Phase.OVER)

    [event] = await diagnosed(scene)
    assert (event.payload["outcome"], event.payload["reason"]) == ("NO_CAUSE", "no_slot")
    assert (event.payload["attempts"], event.payload["deposited"]) == (0, False)
    assert scene.queen.nectar == []
    await scene.stop()


async def test_the_drone_runs_under_a_grant_of_its_own_and_takes_no_slot_of_the_queens() -> None:
    go = asyncio.Event()
    scene = await start_nuc_scene(says_when(go, _CAUSE))
    await _cut(scene)
    await settle(lambda: len(drones(scene)) == 1)

    [drone] = drones(scene)
    assert scene.warden._sub_bee_slots.in_use == 0  # The Queen's grants are not what it runs under.
    assert scene.warden._grants == {}
    assert drone.assignment.grant_id not in scene.warden._grants
    assert "Queen at hive.example." in drone.assignment.objective
    assert drone.binding == "worker"  # The key whose chain runs entirely on this machine.
    assert drone.local is not None and drone.local.origin.value == "WARDEN"
    go.set()
    await settle(lambda: _phase(scene) is Phase.VERIFYING)
    assert scene.warden._sub_bee_slots.in_use == 0
    await scene.stop()


async def test_a_search_left_over_from_an_outage_that_ended_is_finished_before_the_next() -> None:
    scene = await start_nuc_scene(hangs())
    await _cut(scene)
    await settle(lambda: len(drones(scene)) == 1)
    [first] = drones(scene)
    scene.restore()  # The Drone is still working when the link returns ...
    await settle(lambda: scene.warden.state is not WardenState.OFFLINE)

    scene.clock.advance(5.0)  # ... and the link goes again: a new outage, with a new search.
    await _cut(scene)

    await settle(lambda: [d.worker_id for d in drones(scene)] not in ([], [first.worker_id]))
    [event] = await diagnosed(scene)
    assert (event.payload["outcome"], event.payload["reason"]) == ("NO_CAUSE", "link_returned")
    await scene.stop()


async def test_a_guard_that_refuses_the_drones_binding_ends_the_search_without_a_word() -> None:
    scene = await start_nuc_scene(says(_CAUSE))
    refused = BindingRefusedError(scene.assignment.task_id, "worker", "Not for a Drone.")

    async def refuse(*args: object, **kwargs: object) -> object:
        raise refused

    with patch("hivemind.wardens.offline.diagnose.drone.spawn_local", refuse):
        await _cut(scene)
        await settle(lambda: _phase(scene) is Phase.OVER)

    assert drones(scene) == [] and await diagnosed(scene) == []
    assert scene.queen.nectar == []
    await scene.stop()


async def test_a_warden_that_stops_mid_search_leaves_no_drone_behind() -> None:
    scene = await start_nuc_scene(hangs())
    await _cut(scene)
    await settle(lambda: len(drones(scene)) == 1)

    await scene.stop()

    assert drones(scene) == []
