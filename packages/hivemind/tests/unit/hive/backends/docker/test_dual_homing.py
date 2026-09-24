"""Unit tests for DockerCellBackend with a control network: dual-homing and its egress lever.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/backends/docker/
    backend.py (codingrules section 3), split by feature from test_backend.py: roadmap step
    10.6a's control network, over `FakeDockerClient` and `FakeReadinessGate`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.backend for DockerCellBackend, under test.
    - hivemind.hive.backends.docker.network for the dual-homing design.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.base import EgressCutter
from hivemind.hive.backends.bootstrap import NightVeilLink, QueenEndpoint
from hivemind.hive.backends.docker.backend import (
    DockerBackendConfig,
    DockerCellBackend,
    container_name,
)
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.backends.docker.network import ControlNetwork, control_network, network_name
from hivemind.hive.backends.fake import FakeReadinessGate
from hivemind.hive.errors import BackendCapabilityError, CellProvisionError
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import HiveId, new_hive_id, new_node_id

_SUBNET = "10.213.7.0/24"
_GATEWAY_URL = "ws://10.213.7.1:8710"  # The control gateway: where a dual-homed Cell dials.
_LINK = NightVeilLink(
    waggle_url="ws://7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion:8710",
    socks_proxy_url="socks5h://127.0.0.1:9050",
)


def _spec(hive_id: HiveId, **overrides: object) -> VirtualCellSpec:
    """A valid VirtualCellSpec for `hive_id`, EGRESS_ONLY unless overridden."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 1.0,
        "memory_bytes": 1024**3,
        "disk_bytes": 1024**3,
        "capacity": make_capacity(),
        "hive_id": hive_id,
        "network_policy": NetworkPolicy.EGRESS_ONLY,
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _backend(
    url: str = _GATEWAY_URL, *, control: bool = True
) -> tuple[DockerCellBackend, FakeDockerClient, ControlNetwork, HiveId]:
    """A DockerCellBackend over a fake client, with the Hive's control network unless told not."""
    clock = FakeClock()
    hive_id = new_hive_id(clock)
    network = control_network(hive_id, _SUBNET)
    endpoint = QueenEndpoint(
        waggle_url=url,
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex="00" * 32,
        night_veil=_LINK,
    )
    client = FakeDockerClient()
    config = DockerBackendConfig(control=network if control else None)
    backend = DockerCellBackend(client, FakeReadinessGate(clock), endpoint, clock, config)
    return backend, client, network, hive_id


def test_a_control_network_is_what_lets_the_backend_cut_egress() -> None:
    with_control, *_ = _backend()
    without, *_ = _backend(control=False)

    assert with_control.capabilities.can_cut_egress
    assert isinstance(with_control, EgressCutter)
    assert not without.capabilities.can_cut_egress


async def test_a_cell_is_created_on_the_control_network_and_given_its_own_before_it_starts() -> (
    None
):
    backend, client, control, hive_id = _backend()

    cell = await backend.provision(_spec(hive_id))

    assert client.ensure_network_calls == [control.spec]
    assert client.create_container_calls[0].network_name == control.name
    # Attached before the start, so the Cell's first packet already has both networks.
    assert client.connect_network_calls == [(network_name(cell.id), container_name(cell.id))]
    assert client.start_container_calls == [container_name(cell.id)]
    assert client.attached(container_name(cell.id)) == frozenset(
        {control.name, network_name(cell.id)}
    )


async def test_a_dual_homed_cell_must_dial_the_control_gateway() -> None:
    # host.docker.internal is docker0 on native Linux: that link would ride the egress network.
    backend, client, _control, hive_id = _backend("ws://host.docker.internal:8710")

    with pytest.raises(CellProvisionError, match="dials its gateway"):
        await backend.provision(_spec(hive_id))
    assert client.create_container_calls == []
    assert client.networks == {}


async def test_a_night_veil_cell_is_never_dual_homed() -> None:
    # Its link rides Tor, over its own network: the control network would bypass Tor to the host.
    backend, client, _control, hive_id = _backend()
    spec = _spec(
        hive_id,
        image="night-veil-ubuntu",
        network_policy=NetworkPolicy.VPN_TOR,
        comb_shield=CombShieldLevel.NIGHT_VEIL,
    )

    cell = await backend.provision(spec)

    assert client.ensure_network_calls == []
    assert client.create_container_calls[0].network_name == network_name(cell.id)
    with pytest.raises(BackendCapabilityError):
        await backend.cut_egress(cell.id)


async def test_cut_and_restore_move_a_cell_off_and_back_on_its_own_network() -> None:
    backend, client, control, hive_id = _backend()
    cell = await backend.provision(_spec(hive_id))

    await backend.cut_egress(cell.id)
    cut = client.attached(container_name(cell.id))
    await backend.restore_egress(cell.id)

    assert cut == frozenset({control.name})
    assert client.attached(container_name(cell.id)) == frozenset(
        {control.name, network_name(cell.id)}
    )


async def test_without_a_control_network_the_backend_refuses_to_cut() -> None:
    backend, client, _control, hive_id = _backend("ws://host.docker.internal:8710", control=False)
    cell = await backend.provision(_spec(hive_id))

    with pytest.raises(BackendCapabilityError, match="cut_egress"):
        await backend.cut_egress(cell.id)
    assert client.disconnect_network_calls == []


async def test_destroy_and_a_failed_provision_leave_the_hives_control_network_alone() -> None:
    backend, client, control, hive_id = _backend()
    cell = await backend.provision(_spec(hive_id))
    client.set_start_container_failure("no start")

    with pytest.raises(CellProvisionError):
        await backend.provision(_spec(hive_id))
    await backend.destroy(cell.id)

    # Shared by every Cell of the Hive: neither a cleanup nor a destroy ever removes it.
    assert control.name not in client.remove_network_calls
    assert control.name in client.networks
