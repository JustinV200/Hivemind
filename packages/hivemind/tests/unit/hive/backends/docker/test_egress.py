"""Unit tests for hivemind.hive.backends.docker.egress: the cut, the restore, the control network.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/egress.py (codingrules section 3), over `FakeDockerClient`;
    packages/hivemind/tests/integration/test_docker_egress.py proves the same levers against a
    real daemon and a real Cell.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.egress, under test.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.hive.backends.docker.client import ContainerSpec, NetworkSpec
from hivemind.hive.backends.docker.egress import cut_egress, ensure_control, restore_egress
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.backends.docker.network import ControlNetwork, control_network, network_name
from hivemind.hive.errors import BackendCapabilityError, CellEgressError, UnknownCellError
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_hive_id


def _container(cell_id: CellId, network: str) -> ContainerSpec:
    """A Cell's container as the backend creates it, on `network`."""
    return ContainerSpec(
        name=f"hivemind-cell-{cell_id}",
        image="base-ubuntu",
        environment={},
        labels={"hivemind.cell_id": cell_id},
        network_name=network,
        extra_hosts={},
        volume_name=None,
        volume_mount_path="/var/lib/hivemind/scratch",
        nano_cpus=1,
        mem_limit_bytes=1,
        pids_limit=1,
        cap_drop=("ALL",),
        security_opt=(),
        read_only_rootfs=False,
    )


async def _dual_homed_cell() -> tuple[FakeDockerClient, ControlNetwork, CellId, str]:
    """A client holding one Cell on the Hive's control network and its own network."""
    client = FakeDockerClient()
    clock = FakeClock()
    control = control_network(new_hive_id(clock), "10.213.7.0/24")
    cell_id = new_cell_id(clock)
    await ensure_control(client, control)
    await client.create_network(NetworkSpec(name=network_name(cell_id), internal=False, labels={}))
    container = _container(cell_id, control.name)
    await client.create_container(container)
    await client.connect_network(network_name(cell_id), container.name)
    return client, control, cell_id, container.name


async def test_cut_leaves_a_cell_its_control_network_alone() -> None:
    client, control, cell_id, container = await _dual_homed_cell()

    await cut_egress(client, control, cell_id)

    assert client.attached(container) == frozenset({control.name})


async def test_restore_gives_a_cut_cell_its_own_network_back() -> None:
    client, control, cell_id, container = await _dual_homed_cell()
    await cut_egress(client, control, cell_id)

    await restore_egress(client, control, cell_id)

    assert client.attached(container) == frozenset({control.name, network_name(cell_id)})


async def test_a_repeated_cut_or_restore_changes_nothing() -> None:
    client, control, cell_id, container = await _dual_homed_cell()

    await cut_egress(client, control, cell_id)
    await cut_egress(client, control, cell_id)
    after_cuts = client.attached(container)
    await restore_egress(client, control, cell_id)
    await restore_egress(client, control, cell_id)

    assert after_cuts == frozenset({control.name})
    assert client.attached(container) == frozenset({control.name, network_name(cell_id)})


async def test_a_cell_off_the_control_network_is_never_cut() -> None:
    # Provisioned before the control subnet was set, or a VPN_TOR Cell: its link rides its own
    # network, so a cut would take the link with it.
    client = FakeDockerClient()
    control = control_network(new_hive_id(FakeClock()), "10.213.7.0/24")
    cell_id = new_cell_id(FakeClock())
    await client.create_network(NetworkSpec(name=network_name(cell_id), internal=False, labels={}))
    await client.create_container(_container(cell_id, network_name(cell_id)))

    with pytest.raises(BackendCapabilityError, match="cut_egress"):
        await cut_egress(client, control, cell_id)
    assert client.disconnect_network_calls == []


async def test_a_cell_with_no_container_is_unknown() -> None:
    client = FakeDockerClient()
    control = control_network(new_hive_id(FakeClock()), "10.213.7.0/24")

    with pytest.raises(UnknownCellError):
        await restore_egress(client, control, new_cell_id(FakeClock()))


async def test_a_daemon_that_refuses_the_detach_is_an_egress_error() -> None:
    client, control, cell_id, container = await _dual_homed_cell()
    client.set_attach_failure("daemon busy")

    with pytest.raises(CellEgressError, match="daemon busy"):
        await cut_egress(client, control, cell_id)
    assert network_name(cell_id) in client.attached(container)


async def test_ensure_control_refuses_a_same_named_network_that_is_not_internal() -> None:
    client = FakeDockerClient()
    control = control_network(new_hive_id(FakeClock()), "10.213.7.0/24")
    await client.create_network(dataclasses.replace(control.spec, internal=False))

    with pytest.raises(CellEgressError, match="does not match"):
        await ensure_control(client, control)
