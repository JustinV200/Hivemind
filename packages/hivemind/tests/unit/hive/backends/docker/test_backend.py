"""Unit tests for hivemind.hive.backends.docker.backend: DockerCellBackend over the two fakes.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/backend.py (codingrules section 3). The cross-implementation
    contract (idempotent destroy, capability honouring, concurrency safety, ...) lives in
    packages/hivemind/tests/contracts/test_cell_backend_contract.py instead; this module covers
    DockerCellBackend's own extra surface: resource-limit mapping, each NetworkPolicy, cleanup on
    failure at every stage, label-only listing and readiness timeouts (roadmap step 5.4's own
    test list).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.backend for DockerCellBackend, under test.
    - packages/hivemind/tests/contracts/test_cell_backend_contract.py for the shared contract.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.docker.backend import _DEFAULT_PIDS_LIMIT, DockerCellBackend
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.backends.docker.network import network_name
from hivemind.hive.backends.fake import FakeReadinessGate
from hivemind.hive.errors import CellDestroyError, CellProvisionError
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


def _make_spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec, with sensible defaults for every field a test ignores."""
    fields: dict[str, object] = {
        "image": "hivemind/base-ubuntu:dev",
        "cpu_cores": 2.0,
        "memory_bytes": 2 * 1024**3,
        "disk_bytes": 10 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _make_backend(
    clock: FakeClock, *, max_cells: int | None = None
) -> tuple[DockerCellBackend, FakeDockerClient, FakeReadinessGate]:
    """Build a fresh DockerCellBackend over fresh fakes, plus the fakes for direct assertions."""
    client = FakeDockerClient()
    gate = FakeReadinessGate(clock)
    endpoint = QueenEndpoint(
        waggle_url="ws://localhost:8710",
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex="00" * 32,
    )
    backend = DockerCellBackend(client, gate, endpoint, clock, max_cells=max_cells)
    return backend, client, gate


async def test_provision_maps_cpu_memory_and_pids_limit() -> None:
    backend, client, _ = _make_backend(FakeClock())
    spec = _make_spec(cpu_cores=1.5, memory_bytes=3 * 1024**3)

    await backend.provision(spec)

    container = client.create_container_calls[0]
    assert container.nano_cpus == 1_500_000_000
    assert container.mem_limit_bytes == 3 * 1024**3
    assert container.pids_limit == _DEFAULT_PIDS_LIMIT


async def test_provision_sets_least_privilege_flags() -> None:
    backend, client, _ = _make_backend(FakeClock())

    await backend.provision(_make_spec())

    container = client.create_container_calls[0]
    assert container.cap_drop == ("ALL",)
    assert container.security_opt == ("no-new-privileges:true",)
    assert container.read_only_rootfs is True
    assert "/tmp" in container.tmpfs  # noqa: S108  # SAFETY: a container-internal tmpfs path, not a host one.
    assert container.volume_mount_path == "/var/lib/hivemind/scratch"


async def test_provision_stamps_required_labels_over_a_callers_own() -> None:
    backend, client, _ = _make_backend(FakeClock())
    spec = _make_spec(labels={"hivemind.image": "should-not-win", "team": "hive"})

    cell = await backend.provision(spec)

    labels = client.create_container_calls[0].labels
    assert labels["hivemind.hive_id"] == spec.hive_id
    assert labels["hivemind.cell_id"] == cell.id
    assert labels["hivemind.image"] == spec.image  # hivemind.* always wins over a caller's own.
    assert labels["hivemind.comb_shield"] == spec.comb_shield.value
    assert labels["team"] == "hive"  # A caller's own, non-colliding label survives.


@pytest.mark.parametrize(
    ("policy", "expect_internal"),
    [
        (NetworkPolicy.NONE, True),
        (NetworkPolicy.EGRESS_ONLY, False),
    ],
)
async def test_provision_network_policy_sets_internal_flag(
    policy: NetworkPolicy, expect_internal: bool
) -> None:
    backend, client, _ = _make_backend(FakeClock())

    await backend.provision(_make_spec(network_policy=policy))

    assert client.create_network_calls[0].internal is expect_internal


async def test_provision_allowlist_policy_stamps_the_allowlist_label() -> None:
    backend, client, _ = _make_backend(FakeClock())
    spec = _make_spec(
        network_policy=NetworkPolicy.ALLOWLIST, network_allowlist=("api.example.com",)
    )

    await backend.provision(spec)

    assert client.create_network_calls[0].internal is False
    assert client.create_network_calls[0].labels["hivemind.network_allowlist"] == "api.example.com"


async def test_provision_refuses_vpn_tor_on_any_image_but_night_veil_ubuntu() -> None:
    backend, client, gate = _make_backend(FakeClock())
    # image defaults to "hivemind/base-ubuntu:dev" (_make_spec): the in-image kill-switch that
    # image lacks is VPN_TOR's only real enforcement, so this must be refused before anything
    # is created, whatever else the spec asks for.
    spec = _make_spec(network_policy=NetworkPolicy.VPN_TOR, comb_shield=CombShieldLevel.NIGHT_VEIL)

    with pytest.raises(CellProvisionError, match="VPN_TOR"):
        await backend.provision(spec)

    assert client.create_network_calls == []
    assert gate.expect_calls == []


async def test_provision_accepts_vpn_tor_on_the_night_veil_ubuntu_image() -> None:
    backend, client, _ = _make_backend(FakeClock())
    spec = _make_spec(
        image="night-veil-ubuntu",
        network_policy=NetworkPolicy.VPN_TOR,
        comb_shield=CombShieldLevel.NIGHT_VEIL,
    )

    cell = await backend.provision(spec)

    assert cell.comb_shield is CombShieldLevel.NIGHT_VEIL
    # Docker's own network gives unrestricted outbound reach (module docstring: the in-image
    # kill-switch is the real boundary, not Docker's own network layer).
    assert client.create_network_calls[0].internal is False
    assert client.create_network_calls[0].labels["hivemind.network_policy"] == "VPN_TOR"


async def test_provision_cleans_up_on_network_create_failure() -> None:
    backend, client, gate = _make_backend(FakeClock())
    client.set_create_network_failure("name conflict")

    with pytest.raises(CellProvisionError, match="name conflict"):
        await backend.provision(_make_spec())

    assert client.create_volume_calls == []
    assert client.create_container_calls == []
    assert gate.forget_calls == gate.expect_calls  # forgotten exactly what was expected


async def test_provision_cleans_up_on_volume_create_failure() -> None:
    backend, client, gate = _make_backend(FakeClock())
    client.set_create_volume_failure("disk full")

    with pytest.raises(CellProvisionError, match="disk full"):
        await backend.provision(_make_spec())

    assert client.remove_network_calls == [client.create_network_calls[0].name]
    assert client.create_container_calls == []
    assert len(gate.forget_calls) == 1


async def test_provision_cleans_up_on_container_create_failure() -> None:
    backend, client, gate = _make_backend(FakeClock())
    client.set_create_container_failure("image not found")

    with pytest.raises(CellProvisionError, match="image not found"):
        await backend.provision(_make_spec())

    assert client.remove_network_calls == [client.create_network_calls[0].name]
    assert client.remove_volume_calls == [client.create_volume_calls[0].name]
    assert client.start_container_calls == []
    assert len(gate.forget_calls) == 1


async def test_provision_cleans_up_on_container_start_failure() -> None:
    backend, client, gate = _make_backend(FakeClock())
    client.set_start_container_failure("daemon busy")

    with pytest.raises(CellProvisionError, match="daemon busy"):
        await backend.provision(_make_spec())

    assert client.remove_network_calls == [client.create_network_calls[0].name]
    assert client.remove_volume_calls == [client.create_volume_calls[0].name]
    assert client.remove_container_calls == [client.create_container_calls[0].name]
    assert len(gate.forget_calls) == 1


async def test_provision_cleans_up_on_readiness_timeout() -> None:
    clock = FakeClock()
    backend, client, gate = _make_backend(clock)
    spec = _make_spec(ready_timeout_s=5.0)

    # provision() mints its own CellId internally, so this test cannot name it in advance;
    # set_next_never_ready arms whichever Cell the next expect() call registers instead.
    gate.set_next_never_ready()

    task = asyncio.create_task(backend.provision(spec))
    await asyncio.sleep(0)  # Let provision() reach gate.wait_ready's own clock.sleep(5.0) await.
    clock.advance(5.0)  # No real sleeping in tests (codingrules 14.5): drive the fake forward.

    with pytest.raises(CellProvisionError, match="never reported ready"):
        await task

    assert client.remove_network_calls == [client.create_network_calls[0].name]
    assert client.remove_volume_calls == [client.create_volume_calls[0].name]
    assert client.remove_container_calls == [client.create_container_calls[0].name]


async def test_list_cells_reads_from_labels_alone_not_backend_state() -> None:
    clock = FakeClock()
    client = FakeDockerClient()
    gate = FakeReadinessGate(clock)
    endpoint = QueenEndpoint(
        waggle_url="ws://localhost:8710",
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex="00" * 32,
    )
    provisioning_backend = DockerCellBackend(client, gate, endpoint, clock)
    spec = _make_spec()
    cell = await provisioning_backend.provision(spec)

    # A second, independent instance over the same client (a restarted process would look exactly
    # like this): list_cells must still find the Cell, since it never reads provisioning_backend's
    # own in-memory state.
    fresh_backend = DockerCellBackend(client, gate, endpoint, clock)
    records = await fresh_backend.list_cells(spec.hive_id)

    assert [record.cell_id for record in records] == [cell.id]
    assert records[0].image == spec.image


async def test_destroy_is_idempotent_across_a_restarted_backend() -> None:
    clock = FakeClock()
    client = FakeDockerClient()
    gate = FakeReadinessGate(clock)
    endpoint = QueenEndpoint(
        waggle_url="ws://localhost:8710",
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex="00" * 32,
    )
    provisioning_backend = DockerCellBackend(client, gate, endpoint, clock)
    spec = _make_spec()
    cell = await provisioning_backend.provision(spec)

    # A fresh instance, no in-memory state, recomputes every resource name from cell_id alone.
    fresh_backend = DockerCellBackend(client, gate, endpoint, clock)
    await fresh_backend.destroy(cell.id)
    await fresh_backend.destroy(cell.id)  # A second destroy is a silent no-op.

    assert await fresh_backend.list_cells(spec.hive_id) == ()


async def test_destroy_failure_is_a_typed_error() -> None:
    backend, client, _ = _make_backend(FakeClock())
    cell = await backend.provision(_make_spec())
    client.set_remove_container_failure("still in use")

    with pytest.raises(CellDestroyError, match="still in use"):
        await backend.destroy(cell.id)


async def test_destroy_uses_the_deterministic_network_name() -> None:
    backend, client, _ = _make_backend(FakeClock())
    cell = await backend.provision(_make_spec())

    await backend.destroy(cell.id)

    assert client.remove_network_calls[-1] == network_name(cell.id)


async def test_headroom_exceeded_raises_before_creating_anything() -> None:
    backend, client, gate = _make_backend(FakeClock(), max_cells=1)
    await backend.provision(_make_spec())

    with pytest.raises(CellProvisionError, match="headroom"):
        await backend.provision(_make_spec())

    assert len(client.create_container_calls) == 1  # Only the first provision created anything.
    assert len(gate.expect_calls) == 1


async def test_capabilities_headroom_tracks_active_count() -> None:
    backend, _, _ = _make_backend(FakeClock(), max_cells=2)

    assert backend.capabilities.headroom == 2
    cell = await backend.provision(_make_spec())
    assert backend.capabilities.headroom == 1
    await backend.destroy(cell.id)
    assert backend.capabilities.headroom == 2


async def test_pause_and_resume_delegate_to_the_client() -> None:
    backend, client, _ = _make_backend(FakeClock())
    cell = await backend.provision(_make_spec())

    await backend.pause(cell.id)
    await backend.resume(cell.id)

    assert client.pause_container_calls == [client.create_container_calls[0].name]
    assert client.unpause_container_calls == [client.create_container_calls[0].name]
