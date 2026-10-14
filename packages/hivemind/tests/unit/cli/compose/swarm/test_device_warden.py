"""Tests for hivemind.cli.compose.swarm.device_warden: how a device's Level 0 Warden is made.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/swarm/device_warden.py (codingrules section 3). The factory is
    the one a Hive built from a real `builders.swarm.swarm_manifest` holds; its Wardens are built
    but never started, so nothing here leases or dials anything.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.swarm.device_warden for the module under test.
    - hivemind.cli.compose.deps.warden for the bag the Warden is made from.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.forage import make_capacity
from builders.swarm import make_device_party, make_swarm_node, swarm_manifest

from hivemind.cell import CombShieldLevel
from hivemind.cli.compose import Hive, build_hive
from hivemind.cli.compose.deps import HIVE_STAND_LEASE
from hivemind.cli.compose.swarm import LeaseScopedSource, SwarmWardenFactory
from hivemind.forage import ForageCapacity
from hivemind.guard import Capability, CapabilityFamily
from hivemind.manifest import load_manifest
from hivemind.queen.swarm_gate import DeviceWarden
from hivemind.swarm import PollenWatchProbe, ShieldedSource, SwarmNode, SwarmSource
from hivemind.wardens import DeviceDeps
from waggle.clock import FakeClock, SystemClock


def _hive(tmp_path: Path) -> Hive:
    """A Hive with the Swarm on, built and not started."""
    path = swarm_manifest(tmp_path)
    return build_hive(load_manifest(path, {}), environ={}, clock=SystemClock())


def _factory(hive: Hive) -> SwarmWardenFactory:
    """The factory the Hive's gate builds its Wardens with."""
    assert hive.swarm is not None
    factory = hive.swarm.gate.wardens._factory
    assert isinstance(factory, SwarmWardenFactory)
    return factory


def _node(**overrides: object) -> SwarmNode:
    """An enrolled device, FULL access on the Meadow tier unless told otherwise."""
    clock = FakeClock()
    return make_swarm_node(make_device_party(clock), clock, **overrides)


def _built(tmp_path: Path, **overrides: object) -> tuple[Hive, SwarmNode, DeviceWarden]:
    """Build a Hive with the Swarm on and the Warden its factory makes for one node."""
    hive, node = _hive(tmp_path), _node(**overrides)
    return hive, node, _factory(hive).build(node)


def test_the_warden_is_the_nodes_own_bee_and_its_link_carries_the_nodes_cell(
    tmp_path: Path,
) -> None:
    hive, node, built = _built(tmp_path)

    assert built.warden._warden_id == node.warden_id  # The same bee at every rung of the ladder.
    assert built.link.warden_id == node.warden_id
    assert built.link.cell == node.as_cell()
    # It speaks as this process: a Level 0 Warden runs in the Queen's own process.
    assert built.link.node_id == hive.manifest.hive.node_id
    assert built.link.hop.sender == hive.manifest.hive.id
    assert built.link.hop.recipient == node.warden_id


def test_its_lease_needs_the_one_cell_it_borrows_not_the_hive_stand(tmp_path: Path) -> None:
    hive, node, built = _built(tmp_path)

    needed = built.warden._deps.lease_capability

    assert needed == Capability(family=CapabilityFamily.CELL_REAL, scope=node.cell_id)
    assert str(needed) == f"cell:real:{node.cell_id}"
    assert hive.warden._deps.lease_capability == HIVE_STAND_LEASE  # The Hive Stand's is unchanged.


def test_it_is_a_device_warden_that_gives_its_lease_back_when_idle(tmp_path: Path) -> None:
    _, _, built = _built(tmp_path)

    assert built.warden._deps.device == DeviceDeps(release_lease_when_idle=True)


def test_it_watches_its_idle_device_by_asking_the_packet_and_names_no_directory(
    tmp_path: Path,
) -> None:
    hive, _, built = _built(tmp_path)

    watch, stand = built.warden._deps.watch, hive.warden._deps.watch

    assert watch is not None and stand is not None
    assert isinstance(watch.probe, PollenWatchProbe)  # No lease and no command on the device.
    assert watch.bind_session is None  # There is no session of the Hive's to lend it.
    assert watch.settings.roots == ()  # The Hive Stand's directories are not the device's.
    assert watch.settings.interval_s == stand.settings.interval_s  # The numbers are shared.
    assert watch.schedule is not stand.schedule


def test_it_holds_nothing_of_the_hive_stands_own_disk_display_or_models(tmp_path: Path) -> None:
    hive, _, built = _built(tmp_path)
    deps, stand = built.warden._deps, hive.warden._deps

    assert stand.royal_jelly is not None  # The Hive Stand has these; a device's Warden does not.
    assert deps.royal_jelly is None
    assert deps.resident_basket is None
    assert deps.keep_root is None
    assert deps.servers == {}
    assert deps.register_provider is None and deps.unregister_provider is None
    assert stand.recording_store is not None  # The Exoskeleton's flight recorder is the Stand's.
    assert deps.recording_store is None
    assert deps.browser_launcher is None
    assert deps.ears is None


def test_it_shares_the_hives_trail_enforcer_scanner_and_stores(tmp_path: Path) -> None:
    hive, _, built = _built(tmp_path)
    deps, stand = built.warden._deps, hive.warden._deps

    assert deps.trail is stand.trail is hive.stores.trail
    assert deps.memory is stand.memory
    assert deps.enforcer is hive.enforcer
    assert deps.scanner is stand.scanner  # One scanner: every flag on this node is hashed alike.
    assert deps.heartbeat_interval_s == stand.heartbeat_interval_s


def test_a_meadow_devices_source_is_its_swarm_source_scoped_to_each_lease(tmp_path: Path) -> None:
    _, _, built = _built(tmp_path)

    source = built.warden._deps.source

    assert isinstance(source, LeaseScopedSource)
    assert isinstance(source._inner, SwarmSource)
    assert built.hub_listeners == (source._inner,)  # It must hear a dead-man switch's release.


def test_a_propolis_devices_lease_is_verified_before_it_is_returned(tmp_path: Path) -> None:
    _, _, built = _built(tmp_path, comb_shield=CombShieldLevel.PROPOLIS)

    source = built.warden._deps.source

    assert isinstance(source, ShieldedSource)
    assert isinstance(source._inner, LeaseScopedSource)  # Scoped inside: the checks are commands.
    assert isinstance(source._inner._inner, SwarmSource)
    assert built.hub_listeners == (source._inner._inner,)


def test_the_queen_reads_the_devices_capacity_as_the_hub_last_had_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hive, node, built = _built(tmp_path)
    reader = built.link.live_capacity
    assert reader is not None

    async def read() -> ForageCapacity:
        return await reader()

    # Before the hub has heard of it, the capacity the Warden was built on.
    assert asyncio.run(read()) == node.capacity

    # After a capacity report, the hub's record: the grant is sized from the device as it is now.
    fresher = node.model_copy(update={"capacity": make_capacity(max_sub_bees=7)})
    assert hive.swarm is not None
    monkeypatch.setattr(hive.swarm.listener.hub, "node", lambda _device_id: fresher)
    assert asyncio.run(read()).max_sub_bees == 7


def test_each_build_is_a_fresh_warden_over_a_fresh_link_for_the_same_bee(tmp_path: Path) -> None:
    hive, node, first = _built(tmp_path)

    second = _factory(hive).build(node)

    assert second.warden is not first.warden
    assert second.link.transport is not first.link.transport
    assert second.warden._warden_id == first.warden._warden_id == node.warden_id
