"""Tests for hivemind.queen.swarm_gate.reconcile: Wardens kept in line with the registry and hub.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/reconcile.py (codingrules section 3). The registry, hub
    and listener are the real classes over memory (the listener a spy that counts what it is
    asked), a device is a link that is open and silent, and the Wardens are the real class over a
    `FakeCellSource`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.reconcile for the module under test.
"""

from __future__ import annotations

import pytest
from builders.quarantine import settle
from builders.swarm import GateRig

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import DeviceWarden
from hivemind.swarm import (
    GatewayUnavailableError,
    NodeStatus,
    RuntimeLevel,
    SwarmError,
    SwarmNode,
)
from hivemind.swarm.enrolment.capabilities import granted_capabilities
from hivemind.wardens import Warden, WardenState
from waggle.messages.swarm import NodeUpdate


async def _revoke(rig: GateRig, node: SwarmNode) -> SwarmNode:
    """Revoke `node` in the registry the way the operator's command does, event and all."""
    event = rig.hive.recorder.swarm_event(
        "swarm.revoked", node.device_id, {"device_id": node.device_id}
    )
    return await rig.hive.registry.set_status(
        node.device_id, node.status, NodeStatus.REVOKED, rig.clock.now(), event
    )


async def _fresh(rig: GateRig, node: SwarmNode) -> SwarmNode:
    """Return the registry's present row for `node`."""
    row = await rig.hive.registry.node(node.device_id)
    assert row is not None
    return row


async def test_an_online_device_with_a_live_link_gets_a_warden(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)

        await rig.reconciler.sync(node)

        served = rig.wardens.get(node.device_id)
        assert served is not None and served.node == node
        assert [link.warden_id for link in rig.queen.wardens] == [node.warden_id]


async def test_it_keeps_the_warden_it_has_and_tells_it_the_device_is_found(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)
        await rig.reconciler.sync(node)
        served = rig.wardens.get(node.device_id)
        assert served is not None
        await served.warden.cell_lost("closed")
        assert served.warden._cell_lost is True

        await rig.reconciler.sync(node)

        assert rig.wardens.get(node.device_id) is served  # Not served again.
        assert len(rig.factory.built) == 1
        assert served.warden._cell_lost is False  # The device is back, so the loss is over.


async def test_a_device_with_no_live_link_is_reported_lost_to_its_warden(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)
        await rig.reconciler.sync(node)
        await rig.disconnect(node)

        await rig.reconciler.sync(node)

        served = rig.wardens.get(node.device_id)
        assert served is not None and served.warden._cell_lost is True
        assert served.warden.state is WardenState.ACTIVE  # Kept: only told.


async def test_a_device_the_registry_calls_unreachable_is_lost_even_with_its_link_open(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)
        await rig.reconciler.sync(node)
        silent = await rig.hive.registry.set_status(
            node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
        )

        await rig.reconciler.sync(silent)  # Open but silent: the monitor marked it.

        served = rig.wardens.get(node.device_id)
        assert served is not None and served.warden._cell_lost is True


async def test_a_device_that_is_not_connected_gets_no_warden(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()  # Online in the registry, as after a Hive restart; no link yet.

        await rig.reconciler.sync(node)

        assert rig.wardens.get(node.device_id) is None
        assert rig.factory.built == []


async def test_a_device_off_the_gateway_rung_is_not_served_here(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)
        await rig.connect(node)

        await rig.reconciler.sync(node)

        assert rig.wardens.get(node.device_id) is None  # Its Warden is on the device (later step).


async def test_a_revoked_device_is_cut_off_its_warden_stopped_and_its_key_distrusted(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol()
        device = await rig.connect(node)
        await rig.reconciler.sync(node)
        served = rig.wardens.get(node.device_id)
        assert served is not None
        revoked = await _revoke(rig, node)

        await rig.reconciler.sync(revoked)

        assert rig.listener.disconnected == [node.device_id]
        assert not device.link.is_open
        assert served.warden.state is WardenState.STOPPED
        assert rig.wardens.get(node.device_id) is None
        assert rig.queen.wardens == ()
        assert rig.listener.key_syncs == 1


async def test_the_keys_are_re_read_once_per_revocation_not_once_per_pass(rig: GateRig) -> None:
    node = await rig.enrol()
    revoked = await _revoke(rig, node)

    for _ in range(3):
        await rig.reconciler.sync(revoked)

    assert rig.listener.key_syncs == 1


async def test_a_changed_access_level_sends_the_device_its_terms_and_rebuilds_the_warden(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol()
        device = await rig.connect(node)
        await rig.reconciler.sync(node)
        old = rig.wardens.get(node.device_id)
        assert old is not None
        await rig.hive.registry.set_access(
            node.device_id,
            AccessLevel.READ_ONLY,
            granted_capabilities(AccessLevel.READ_ONLY, node.cell_id),
        )
        changed = await _fresh(rig, node)

        await rig.reconciler.sync(changed)

        update = (await device.sent()).payload
        assert isinstance(update, NodeUpdate)
        assert update.access_level.name == "READ_ONLY"
        new = rig.wardens.get(node.device_id)
        assert new is not None and new is not old
        assert old.warden.state is WardenState.STOPPED
        assert new.built.link.cell.access_level is AccessLevel.READ_ONLY  # The Cell carries it.
        assert new.node.access_level is AccessLevel.READ_ONLY
        assert rig.hive.hub.node(node.device_id) == changed  # The hub's record is the new one too.
        assert [link.warden_id for link in rig.queen.wardens] == [node.warden_id]


async def test_a_changed_tier_rebuilds_the_warden_too(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)
        await rig.reconciler.sync(node)
        await rig.hive.registry.set_shield(node.device_id, CombShieldLevel.PROPOLIS)

        await rig.reconciler.sync(await _fresh(rig, node))

        assert len(rig.factory.built) == 2
        assert rig.factory.built[1].link.cell.comb_shield is CombShieldLevel.PROPOLIS


async def test_terms_that_could_not_be_sent_leave_the_warden_as_it_was(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        device = await rig.connect(node)
        await rig.reconciler.sync(node)
        old = rig.wardens.get(node.device_id)
        await rig.hive.registry.set_access(
            node.device_id,
            AccessLevel.READ_ONLY,
            granted_capabilities(AccessLevel.READ_ONLY, node.cell_id),
        )
        await device.device_end.close()  # The link is failing: the send cannot go.

        await rig.reconciler.sync(await _fresh(rig, node))

        assert rig.wardens.get(node.device_id) is old  # Tried again at the next pass.
        assert len(rig.factory.built) == 1


async def test_a_warden_whose_loop_ended_is_replaced_by_a_fresh_one(rig: GateRig) -> None:
    rig.factory.warden_class = _DyingWarden
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)
        await rig.reconciler.sync(node)
        dead = rig.wardens.get(node.device_id)
        assert dead is not None
        await settle(lambda: dead.has_ended)
        rig.factory.warden_class = Warden

        await rig.reconciler.sync(node)

        fresh = rig.wardens.get(node.device_id)
        assert fresh is not None and fresh is not dead and not fresh.has_ended
        assert len(rig.factory.built) == 2
        assert [link.warden_id for link in rig.queen.wardens] == [node.warden_id]


async def test_one_devices_failure_does_not_stop_the_pass(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        bad, good = await rig.enrol(), await rig.enrol()
        await rig.connect(bad)
        await rig.connect(good)
        build = rig.factory.build

        def failing(node: SwarmNode) -> DeviceWarden:
            if node.device_id == bad.device_id:
                raise GatewayUnavailableError("The device went away while its Warden was built.")
            return build(node)

        monkeypatch.setattr(rig.factory, "build", failing)

        await rig.reconciler.sweep()  # Must not raise.

        assert rig.wardens.get(bad.device_id) is None
        assert rig.wardens.get(good.device_id) is not None


async def test_a_sweep_reads_every_enrolled_node(rig: GateRig) -> None:
    async with rig.serving():
        nodes = [await rig.enrol() for _ in range(3)]
        for node in nodes:
            await rig.connect(node)

        await rig.reconciler.sweep()

        assert set(rig.wardens.device_ids()) == {node.device_id for node in nodes}
        spawned = await rig.queen_deps.trail.query(TrailQuery(kind="warden.spawned"))
        assert len(spawned) == 3


class _DyingWarden(Warden):
    """A Warden whose first tick fails the way a device going away mid-command does."""

    async def _tick(self) -> None:
        raise SwarmError("The device went away mid-command.")
