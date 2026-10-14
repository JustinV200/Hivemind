"""Tests for hivemind.queen.swarm_gate.reconcile: the terms of a device whose Warden is on it.

A Level 0 device's Warden is rebuilt by the pass when the operator changes the device's terms. A
device whose Warden runs on it is told in two parts: its packet at once, on the first pass that
finds the terms different and whatever its Warden is doing, and the Warden itself by a restart
(the renewal), which the pass starts. A Nuc whose Warden connected afresh is also looked at here,
since the registry must not go on saying it has a server it has not. These tests drive the real
reconciler against the renewal scenario.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/reconcile.py (codingrules section 3), split by feature
    (14.2) from ../test_reconcile.py, which covers a Level 0 device.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.reconcile for the module under test.
"""

from __future__ import annotations

import pytest
from builders.quarantine import settle
from builders.swarm import (
    GateRig,
    ScriptedPacket,
    make_nuc_device,
    make_renewal,
    restart_nuc_warden,
)

from hivemind.cell import AccessLevel
from hivemind.queen.swarm_gate.colonize import steps as steps_module
from hivemind.swarm import RuntimeLevel
from hivemind.swarm.operator import OperatorDesk
from waggle.messages.swarm import NucAction

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.


async def test_a_packet_is_sent_the_new_terms_at_once_even_while_its_warden_is_away(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)
        device = await rig.connect(node)  # The packet is up; no Warden has dialled in.
        packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
        desk = OperatorDesk(rig.hive.registry, rig.hive.recorder)
        changed = (await desk.set_access(node, AccessLevel.SCRATCH)).node

        await rig.reconciler.sync(changed)
        await settle(lambda: bool(packet.updates))

        [update] = packet.updates
        assert update.access_level.value == "SCRATCH"
        hub_node = rig.hive.hub.node(node.device_id)
        assert hub_node is not None and hub_node.access_level is AccessLevel.SCRATCH
        await packet.close()


async def test_a_packet_that_already_has_the_terms_is_not_told_again(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)
        device = await rig.connect(node)
        packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
        desk = OperatorDesk(rig.hive.registry, rig.hive.recorder)
        changed = (await desk.set_access(node, AccessLevel.SCRATCH)).node
        await rig.reconciler.sync(changed)

        await rig.reconciler.sync(changed)
        await rig.reconciler.sync(changed)

        await settle(lambda: bool(packet.updates))
        assert len(packet.updates) == 1
        await packet.close()


async def test_a_send_that_failed_is_tried_again_on_the_next_pass(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = steps_module.send_terms
    answers = iter([False])

    async def flaky(*args: object, **kwargs: object) -> bool:
        return next(answers, True) and await sent(*args, **kwargs)  # type: ignore[arg-type]

    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)
        device = await rig.connect(node)
        packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
        desk = OperatorDesk(rig.hive.registry, rig.hive.recorder)
        changed = (await desk.set_access(node, AccessLevel.SCRATCH)).node
        monkeypatch.setattr(steps_module, "send_terms", flaky)

        await rig.reconciler.sync(changed)  # The link refused it.
        assert packet.updates == []
        await rig.reconciler.sync(changed)  # Nothing was assumed: it goes now.

        await settle(lambda: bool(packet.updates))
        assert len(packet.updates) == 1
        await packet.close()


async def test_the_pass_restarts_a_warden_that_runs_under_terms_the_operator_changed(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        row = await scenario.narrow(AccessLevel.SCRATCH)

        await rig.reconciler.sync(row)
        assert rig.colonizer.busy(row.device_id)  # The renewal owns the device now.
        await scenario.finish()

        # The pass told the packet; the renewal found it told and sent nothing more.
        assert scenario.packet.log == ["update", "colonize:WITHDRAW", "colonize:START"]
        assert not await scenario.renew()  # The new Warden runs under the registry's terms.
        await scenario.close()


async def test_the_pass_leaves_a_device_alone_while_a_flow_owns_it(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        row = await scenario.narrow(AccessLevel.SCRATCH)
        await rig.reconciler.sync(row)
        assert rig.colonizer.busy(row.device_id)

        await rig.reconciler.sync(row)  # Mid-flow: the pass must not get in its way.

        await scenario.finish()
        assert len(scenario.started) == 1  # One restart, however often the pass looked.
        await scenario.close()


async def test_the_pass_starts_a_nucs_server_again_once_when_its_warden_connected_afresh(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        assert await rig.colonizer.request(device.node)
        await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)
        warden = await restart_nuc_warden(rig, device, "box")
        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.level is RuntimeLevel.NUC

        await rig.reconciler.sync(row)
        await settle(lambda: not rig.colonizer.busy(row.device_id), _FLOW_ROUNDS)
        await rig.reconciler.sync(row)  # Proven now: nothing more to ask.
        await settle(lambda: not rig.colonizer.busy(row.device_id), _FLOW_ROUNDS)

        assert warden.actions == [NucAction.PROMOTE]
        await device.close()


async def test_a_wish_to_lower_a_nuc_is_carried_out_instead_of_starting_its_server_again(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        assert await rig.colonizer.request(device.node)
        await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)
        warden = await restart_nuc_warden(rig, device, "box")
        await rig.hive.registry.set_desired_level(device.node.device_id, RuntimeLevel.COLONIZED)
        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None

        await rig.reconciler.sync(row)
        await settle(lambda: not rig.colonizer.busy(row.device_id), _FLOW_ROUNDS)

        assert warden.actions == [NucAction.DEMOTE]  # No server was started to be stopped again.
        after = await rig.hive.registry.node(row.device_id)
        assert after is not None and after.level is RuntimeLevel.COLONIZED
        await device.close()
