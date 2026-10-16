"""Tests for hivemind.queen.swarm_gate.colonize.promote: a colonization that fails is undone.

Any step of a promotion can fail, and each failure must leave the Hive as it was: the packet told
to withdraw, a Warden that did dial in let go, the rung put back if it had moved, the Level 0 Warden
rebuilt on the Hive Stand, the paused tasks resumed there, the failure on the trail as one stable
word, and the operator's request cleared so it is not tried again on every pass.

Fits into the Hive:
    Mirrors the rollback half of src/hivemind/queen/swarm_gate/colonize/promote.py (codingrules
    section 3, split by feature per 14.2); the happy path is in test_promote.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.promote for the module under test.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.isolation import place_running
from builders.swarm import GateRig, PromotionScenario, make_promotion

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize import promote as promote_module
from hivemind.swarm import GatewayUnavailableError, LadderError, NodeStatus, RuntimeLevel
from hivemind.wardens import WardenState
from waggle.messages.swarm import Colonize, ColonizeAction, ColonizeOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns to wait for the flow to get where a test wants it.


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def _undone(scenario: PromotionScenario) -> None:
    """The rig left exactly as a failed colonization should: Level 0, request answered."""
    node = await scenario.node()
    assert node.level is RuntimeLevel.GATEWAY and node.desired_level is None
    assert not scenario.rig.colonizer.busy(scenario.device_id)


async def test_a_prepare_that_fails_changes_nothing_and_says_why(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        old = rig.wardens.get(scenario.device_id)
        scenario.packet.outcomes[ColonizeAction.PREPARE] = ColonizeOutcome.FAILED

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)
        assert rig.wardens.get(scenario.device_id) is old  # Its Warden never left.
        task = await rig.queen_deps.chamber.get(scenario.placed.task.id)
        assert task.status is TaskStatus.RUNNING  # Never paused.
        assert scenario.packet.actions == [ColonizeAction.PREPARE, ColonizeAction.WITHDRAW]
        assert ("colonize_failed", "prepare_failed") in await _decisions(rig)
        assert await rig.hive.events("swarm.demoted") == []  # The Warden never moved.
        await scenario.close()


async def test_a_packet_that_refuses_prepare_is_the_same_failure_with_its_own_word(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.refused.add(ColonizeAction.PREPARE)

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)
        assert ("colonize_failed", "packet_refused") in await _decisions(rig)
        await scenario.close()


async def test_a_link_lost_mid_flow_is_not_asked_to_withdraw(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.silent.add(ColonizeAction.PREPARE)

        async def drop(order: Colonize) -> None:
            await scenario.placed.device.link.close()

        scenario.packet.before[ColonizeAction.PREPARE] = drop

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)
        assert scenario.packet.actions == [ColonizeAction.PREPARE]  # Nobody to withdraw from.
        assert ("colonize_failed", "link_lost") in await _decisions(rig)
        await scenario.close()


async def test_a_start_that_fails_puts_the_warden_back_on_the_hive_stand(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        old = rig.wardens.get(scenario.device_id)
        assert old is not None
        scenario.packet.outcomes[ColonizeAction.START] = ColonizeOutcome.FAILED

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)
        assert scenario.packet.actions == [
            ColonizeAction.PREPARE,
            ColonizeAction.START,
            ColonizeAction.WITHDRAW,
        ]
        rebuilt = rig.wardens.get(scenario.device_id)
        assert rebuilt is not None and rebuilt is not old  # A new Level 0 Warden, the same id.
        assert old.warden.state is WardenState.STOPPED
        assert [link.node_id for link in rig.queen.wardens] == [None]  # Its link, not a device's.
        task = await rig.queen_deps.chamber.get(scenario.placed.task.id)
        assert (
            task.status is TaskStatus.RUNNING and task.warden_id == scenario.placed.node.warden_id
        )
        [demoted] = await rig.hive.events("swarm.demoted")
        assert demoted.payload["reason"] == "start_failed"
        assert (demoted.payload["from_level"], demoted.payload["to_level"]) == (
            "COLONIZED",
            "GATEWAY",
        )
        assert await rig.hive.events("swarm.promoted") == []
        assert await rig.queen_deps.trail.query(TrailQuery(kind="warden.migrated")) == ()
        await scenario.close()


async def test_a_warden_that_never_dials_in_undoes_the_move_after_the_wait(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(promote_module, "ATTACH_TIMEOUT_S", 0.05)
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.on_start = None  # START is answered, and nobody dials.

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)
        [demoted] = await rig.hive.events("swarm.demoted")
        assert demoted.payload["reason"] == "warden_never_attached"
        assert rig.wardens.get(scenario.device_id) is not None  # Rebuilt.
        task = await rig.queen_deps.chamber.get(scenario.placed.task.id)
        assert task.status is TaskStatus.RUNNING
        await scenario.close()


async def test_a_warden_that_dialled_in_is_let_go_when_the_rung_cannot_be_set(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        async def refuse(*_args: object, **_kwargs: object) -> object:
            raise LadderError(RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED)

        monkeypatch.setattr(rig.hive.registry, "set_level", refuse)

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)
        assert len(scenario.dialled) == 1  # It did arrive, and was dismissed.
        assert [link.node_id for link in rig.queen.wardens] == [None]  # Only the Level 0 link.
        [demoted] = await rig.hive.events("swarm.demoted")
        assert demoted.payload["reason"] == "level_not_set"
        await scenario.close()


async def test_a_task_placed_while_the_warden_was_on_its_device_is_taken_back_by_the_undo(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        deps = rig.queen_deps
        slipped: list[Task] = []

        async def refuse(*_args: object, **_kwargs: object) -> object:
            # Between the device Warden's arrival and the commit the Cell is closed to the
            # dispatcher, whatever the attach did with it...
            assert deps.dispatch.held.is_held(scenario.placed.node.cell_id)
            # ...and a placement that reaches it all the same has no bee once the undo dismisses
            # that Warden, so the undo must take it back rather than leave it RUNNING there.
            device_warden = next(link for link in rig.queen.wardens if link.node_id is not None)
            slipped.append(await place_running(deps, device_warden))
            raise LadderError(RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED)

        monkeypatch.setattr(rig.hive.registry, "set_level", refuse)

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)
        [task] = slipped
        row = await deps.chamber.get(task.id)
        assert row.status is TaskStatus.PENDING and row.warden_id is None  # Placed afresh.
        assert ("tasks_taken_back", "undone") in await _decisions(rig)
        paused = await deps.chamber.get(scenario.placed.task.id)
        assert paused.status is TaskStatus.RUNNING  # The paused one resumed on the Hive Stand.
        await scenario.close()


async def test_a_failure_after_the_rung_moved_puts_the_rung_back(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        async def fail(*_args: object, **_kwargs: object) -> None:
            raise GatewayUnavailableError("The trail could not be written.")

        monkeypatch.setattr(promote_module, "record_migrated", fail)

        await scenario.start()
        await scenario.finish()

        await _undone(scenario)  # Back on GATEWAY, in the registry too.
        assert len(await rig.hive.events("swarm.promoted")) == 1  # It had moved...
        assert len(await rig.hive.events("swarm.demoted")) == 1  # ...and was put back.
        recorded = rig.hive.hub.node(scenario.device_id)
        assert recorded is not None and recorded.level is RuntimeLevel.GATEWAY
        task = await rig.queen_deps.chamber.get(scenario.placed.task.id)
        assert task.status is TaskStatus.RUNNING  # Resumed on the Hive Stand's Warden.
        await scenario.close()


async def test_a_device_revoked_during_the_move_is_not_rebuilt_and_its_tasks_move_on(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.outcomes[ColonizeAction.START] = ColonizeOutcome.FAILED

        async def revoke(order: Colonize) -> None:
            node = await scenario.node()
            event = rig.hive.recorder.swarm_event("swarm.revoked", node.device_id, {})
            await rig.hive.registry.set_status(
                node.device_id, node.status, NodeStatus.REVOKED, rig.clock.now(), event
            )

        scenario.packet.on_start = revoke

        await scenario.start()
        await scenario.finish()

        assert rig.wardens.get(scenario.device_id) is None  # Nothing rebuilt for a removed device.
        task = await rig.queen_deps.chamber.get(scenario.placed.task.id)
        assert task.status is TaskStatus.PENDING  # To be placed on whatever fits.
        assert await rig.hive.events("swarm.demoted") == []
        await scenario.close()


async def test_a_flow_cancelled_after_the_move_began_takes_its_tasks_back(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.on_start = None  # START is answered; the wait for the Warden begins.
        await scenario.start()
        task_id = scenario.placed.task.id
        await _until_paused(rig, task_id)

        await rig.colonizer.stop()  # The gate is stopping.

        assert not rig.colonizer.busy(scenario.device_id)
        task = await rig.queen_deps.chamber.get(task_id)
        assert task.status is TaskStatus.PENDING  # Not left PAUSED for good.
        assert ("tasks_taken_back", "gate_stopping") in await _decisions(rig)
        await scenario.close()


async def _until_paused(rig: GateRig, task_id: str) -> None:
    """Wait until the flow has paused the task, which is where it is about to stop the Warden."""
    for _ in range(_FLOW_ROUNDS):
        if (await rig.queen_deps.chamber.get(task_id)).status is TaskStatus.PAUSED:  # type: ignore[arg-type]
            return
        await asyncio.sleep(0)
    raise AssertionError("The task was never paused.")
