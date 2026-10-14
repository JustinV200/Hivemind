"""Tests for hivemind.queen.swarm_gate.colonize.renew when a step fails: the fallback to Level 0.

A renewal that fails leaves the old Warden possibly running under terms wider than the operator now
allows, and that must not stand. So whichever step failed (the packet would not stop the Warden or
start one, the new Warden never dialled in, or it came up under the old terms after all) the device
falls back to the one place the Queen can always enforce the registry's terms: its Warden is let go,
the rung is lowered to Level 0 (through the colonized rung for a Nuc), a Level 0 Warden is built
from the registry's row, and the tasks the renewal paused resume there. A renewal cut short by the
gate stopping takes its paused tasks back instead of leaving them PAUSED.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/renew/fallback.py and flow.py (codingrules
    section 3), split by feature (14.2) from test_flow.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.renew.fallback for the module under test.
"""

from __future__ import annotations

import pytest
from builders.quarantine import settle
from builders.swarm import GateRig, RenewalScenario, make_renewal

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import AccessLevel
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize import steps as steps_module
from hivemind.queen.swarm_gate.colonize.renew import flow as renew_flow
from hivemind.swarm import NodeStatus, RuntimeLevel
from waggle.messages.swarm import Colonize, ColonizeAction, ColonizeOutcome

_SHORT_S = 0.05  # A deadline and a margin short enough for a silent packet to be given up on.
_ROUNDS = 40_000  # Scheduling turns a whole flow may take.


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def _fell_back(rig: GateRig, scenario: RenewalScenario, word: str) -> None:
    """Assert the device is a Level 0 one, served here, its task running and `word` recorded."""
    row = await scenario.node()
    assert row.level is RuntimeLevel.GATEWAY and row.desired_level is None
    assert rig.wardens.get(row.device_id) is not None  # The Hive Stand's Warden, built afresh.
    status = (await rig.queen_deps.chamber.get(scenario.task.id)).status
    assert status is TaskStatus.RUNNING  # Resumed there from its Handoff: never failed.
    assert ("renew_failed", word) in await _decisions(rig)
    assert not rig.queen_deps.dispatch.held.is_held(row.cell_id)
    assert not rig.colonizer.busy(row.device_id)


async def test_a_packet_that_will_not_stop_the_warden_leaves_the_device_a_level_0_one(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        del scenario.packet.before[ColonizeAction.WITHDRAW]  # The Warden is not stopped.
        scenario.packet.outcomes[ColonizeAction.WITHDRAW] = ColonizeOutcome.FAILED
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        await _fell_back(rig, scenario, "withdraw_failed")
        [demoted] = await rig.hive.events("swarm.demoted")
        assert (demoted.payload["from_level"], demoted.payload["to_level"]) == (
            "COLONIZED",
            "GATEWAY",
        )
        assert "withdraw_failed" in str(demoted.payload["reason"])
        # Asked again to stop whatever it started, since the first answer left it unknown.
        assert scenario.packet.log == ["update", "colonize:WITHDRAW", "colonize:WITHDRAW"]
        await scenario.close()


async def test_a_packet_that_will_not_start_a_warden_leaves_the_device_a_level_0_one(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        scenario.packet.outcomes[ColonizeAction.START] = ColonizeOutcome.FAILED
        scenario.packet.on_start = None  # The Warden program was never started.
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        await _fell_back(rig, scenario, "start_failed")
        assert scenario.started == []  # No Warden ever dialled in.
        await scenario.close()


async def test_a_warden_that_never_dials_in_leaves_the_device_a_level_0_one(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renew_flow, "ATTACH_TIMEOUT_S", _SHORT_S)
    async with rig.serving():
        scenario = await make_renewal(rig)
        scenario.packet.on_start = None  # It says STARTED and nothing comes.
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        await _fell_back(rig, scenario, "warden_never_attached")
        await scenario.close()


async def test_a_warden_that_comes_up_under_the_old_terms_is_never_trusted(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.narrow(AccessLevel.SCRATCH)
        scenario.stale_terms = True  # The packet started it from terms it was never sent.

        assert await scenario.renew()
        await scenario.finish()

        await _fell_back(rig, scenario, "terms_not_applied")
        [stale] = scenario.started
        assert not stale.is_connected  # Let go: it holds terms the operator took away.
        await scenario.close()


async def test_a_packet_that_cannot_be_reached_is_not_asked_again(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renew_flow, "WITHDRAW_DEADLINE_S", _SHORT_S)
    monkeypatch.setattr(steps_module, "REPLY_MARGIN_S", _SHORT_S)
    async with rig.serving():
        scenario = await make_renewal(rig)
        scenario.packet.silent.add(ColonizeAction.WITHDRAW)
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        await _fell_back(rig, scenario, "packet_silent")
        # A second WITHDRAW would only wait out another deadline for a packet that cannot answer.
        assert scenario.packet.log == ["update", "colonize:WITHDRAW"]
        await scenario.close()


async def test_a_nuc_falls_back_through_the_colonized_rung_and_its_sources_leave_the_map(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig, nuc=True)
        scenario.packet.outcomes[ColonizeAction.START] = ColonizeOutcome.FAILED
        scenario.packet.on_start = None
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        await _fell_back(rig, scenario, "start_failed")
        steps = [
            (e.payload["from_level"], e.payload["to_level"])
            for e in await rig.hive.events("swarm.demoted")
        ]
        assert steps == [("NUC", "COLONIZED"), ("COLONIZED", "GATEWAY")]  # One edge at a time.
        assert [s for s in rig.queen_deps.map.sources() if s.spec.host_cell_id] == []
        await scenario.close()


async def test_a_device_the_operator_revoked_meanwhile_gets_no_warden_and_loses_no_task(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)

        async def revoke(order: Colonize) -> None:
            node = await scenario.node()
            event = rig.hive.recorder.swarm_event("swarm.revoked", node.device_id, {})
            await rig.hive.registry.set_status(
                node.device_id, node.status, NodeStatus.REVOKED, rig.clock.now(), event
            )

        scenario.packet.outcomes[ColonizeAction.START] = ColonizeOutcome.FAILED
        scenario.packet.on_start = None
        scenario.packet.before[ColonizeAction.START] = revoke
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        assert rig.wardens.get(scenario.device.node.device_id) is None
        status = (await rig.queen_deps.chamber.get(scenario.task.id)).status
        assert status is TaskStatus.PENDING  # Taken back, to be placed on another Cell.
        await scenario.close()


async def test_stopping_the_gate_mid_renewal_takes_the_paused_tasks_back(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        scenario.packet.silent.add(ColonizeAction.WITHDRAW)  # The renewal waits on the packet.
        await scenario.narrow(AccessLevel.SCRATCH)
        assert await scenario.renew()
        await settle(lambda: scenario.packet.log[-1:] == ["colonize:WITHDRAW"], _ROUNDS)

        await rig.colonizer.stop()  # The gate is stopping.

        assert not rig.colonizer.busy(scenario.device.node.device_id)
        status = (await rig.queen_deps.chamber.get(scenario.task.id)).status
        assert status is TaskStatus.PENDING  # Not left PAUSED.
        assert not rig.queen_deps.dispatch.held.is_held(scenario.device.node.cell_id)
        await scenario.close()
