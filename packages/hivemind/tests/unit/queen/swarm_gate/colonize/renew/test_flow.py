"""Tests for hivemind.queen.swarm_gate.colonize.renew.flow: a colonized device's terms change.

The operator narrows (or widens) a device whose Warden runs on it, and the Warden keeps the terms
it was started under until it is started again. These tests run the whole renewal against a
device: its packet is scripted to stop the Warden when ordered and to start a new one that dials in
under the registry's terms, and its Warden is a scripted end of a connection the gate's door
admitted, with a task running on it. What is asserted is the order (the packet is told first, the
tasks are checkpointed before the Warden is stopped), that nothing waits for the device to go idle,
that the rung and the Warden's id stay, that a Nuc stays a Nuc, and that every task resumes.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/renew/flow.py (codingrules section 3); the
    failures and the fallback are in test_flow_fallback.py (split by feature, 14.2).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.renew.flow for the module under test.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the choice of restart.
"""

from __future__ import annotations

import pytest
from builders.quarantine import settle
from builders.swarm import GateRig, RenewalScenario, make_renewal
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.queen.swarm_gate.colonize import steps as steps_module
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import NucAction, NucOutcome


async def _status(rig: GateRig, scenario: RenewalScenario) -> TaskStatus:
    """The status of the task that was running on the device."""
    return (await rig.queen_deps.chamber.get(scenario.task.id)).status


async def test_the_packet_is_told_first_then_the_warden_is_stopped_and_started_again(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        assert scenario.packet.log == ["update", "colonize:WITHDRAW", "colonize:START"]
        [update] = scenario.packet.updates
        assert update.access_level.value == "SCRATCH"  # Its own checks narrow at once.
        [started] = scenario.started
        assert started is scenario.device.warden
        await scenario.close()


async def test_the_new_warden_runs_under_the_terms_so_the_device_is_not_renewed_again(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)
        assert await scenario.renew()
        await scenario.finish()

        assert not await scenario.renew()  # Its `cell.ready` names the terms the registry holds.
        assert not rig.colonizer.busy(scenario.device.node.device_id)
        [link] = rig.queen.wardens
        assert link.transport.is_connected and link.warden_id == scenario.device.node.warden_id
        await scenario.close()


async def test_a_change_of_tier_is_renewed_like_a_change_of_access_level(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.reshield(CombShieldLevel.PROPOLIS)

        assert await scenario.renew()
        await scenario.finish()

        assert scenario.packet.log == ["update", "colonize:WITHDRAW", "colonize:START"]
        assert not await scenario.renew()
        await scenario.close()


async def test_a_widening_reaches_the_warden_the_same_way(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)
        assert await scenario.renew()
        await scenario.finish()

        await scenario.narrow(AccessLevel.FULL)

        assert await scenario.renew()
        await scenario.finish()
        assert len(scenario.started) == 2
        await scenario.close()


async def test_nothing_waits_for_the_device_to_go_idle_the_running_task_is_checkpointed(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        first = scenario.first
        await scenario.narrow(AccessLevel.SCRATCH)
        assert await _status(rig, scenario) is TaskStatus.RUNNING  # Busy when the change lands.

        assert await scenario.renew()
        await scenario.finish()

        [lever] = first.intervenes  # The bee was told to write its Handoff before it was stopped.
        assert lever.task_id == scenario.task.id
        assert [pause.task_id for pause in first.pauses] == [scenario.task.id]
        await scenario.close()


async def test_every_task_resumes_on_the_new_warden_and_none_is_failed(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        assert await _status(rig, scenario) is TaskStatus.RUNNING
        await settle(lambda: bool(scenario.started[0].assignments))
        [assignment] = scenario.started[0].assignments
        assert assignment.task_id == scenario.task.id
        assert not rig.queen_deps.dispatch.held.is_held(scenario.device.node.cell_id)
        await scenario.close()


async def test_work_that_had_no_bee_is_taken_back_to_be_placed_afresh(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        deps = rig.queen_deps
        link = rig.queen.wardens[0]
        [waiting] = await deps.chamber.submit(make_graph_draft({"root": ()}))
        await deps.chamber.assign(
            waiting.id, link.warden_id, link.cell.id, "Placed.", bound_tier=link.cell.comb_shield
        )
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        assert (await deps.chamber.get(waiting.id)).status is TaskStatus.PENDING  # No bee had it.
        await scenario.close()


async def test_the_rung_and_the_wardens_id_do_not_change(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        row = await scenario.node()
        assert (
            row.level is RuntimeLevel.COLONIZED and row.warden_id == scenario.device.node.warden_id
        )
        assert await rig.hive.events("swarm.promoted") == []
        assert await rig.hive.events("swarm.demoted") == []
        await scenario.close()


async def test_a_device_whose_warden_runs_under_the_registrys_terms_is_left_alone(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)

        assert not await scenario.renew()
        assert scenario.packet.log == []
        await scenario.close()


async def test_a_device_whose_warden_has_not_reported_terms_is_left_alone(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)
        rig.colonizer._desk.forget(scenario.device.node.device_id)  # Nothing is known of it.

        assert not await scenario.renew()
        await scenario.close()


async def test_a_renewal_that_is_under_way_owns_the_device(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)
        assert await scenario.renew()

        assert rig.colonizer.busy(scenario.device.node.device_id)
        assert await scenario.renew()  # The next pass finds it still owned.
        await scenario.finish()
        await scenario.close()


async def test_a_packet_that_cannot_be_told_changes_nothing_and_is_tried_again(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*args: object, **kwargs: object) -> bool:
        return False

    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)
        monkeypatch.setattr(steps_module, "send_terms", failing)

        assert await scenario.renew()
        await scenario.finish()

        assert await _status(rig, scenario) is TaskStatus.RUNNING  # Nothing was paused.
        assert scenario.packet.log == []  # Nothing was stopped: a Warden started now is stale.
        assert await scenario.renew()  # The difference is still there, so the next pass tries.
        await scenario.finish()
        await scenario.close()


async def test_a_nuc_stays_a_nuc_its_server_is_started_again_before_its_tasks_resume(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig, nuc=True)
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        row = await scenario.node()
        assert row.level is RuntimeLevel.NUC
        new = scenario.started[0]
        [order] = new.orders
        assert order.action is NucAction.PROMOTE and order.server_id == "box"
        await settle(lambda: "assign" in new.received)
        assert new.received.index("order:PROMOTE") < new.received.index("assign")
        assert await _status(rig, scenario) is TaskStatus.RUNNING
        assert len(await rig.hive.events("swarm.demoted")) == 0
        await scenario.close()


async def test_a_nuc_whose_server_cannot_start_again_is_lowered_cleanly_and_still_renewed(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig, nuc=True)
        scenario.server = NucOutcome.FAILED  # The new Warden cannot start the server again.
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        row = await scenario.node()
        assert row.level is RuntimeLevel.COLONIZED  # Lowered by the restoration, not by a fallback.
        [lowered] = await rig.hive.events("swarm.demoted")
        assert (lowered.payload["from_level"], lowered.payload["to_level"]) == ("NUC", "COLONIZED")
        assert await _status(rig, scenario) is TaskStatus.RUNNING  # And the task resumed.
        assert not await scenario.renew()  # The Warden runs under the new terms.
        await scenario.close()
