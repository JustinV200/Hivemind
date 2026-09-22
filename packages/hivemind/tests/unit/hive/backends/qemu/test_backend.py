"""Unit tests for hivemind.hive.backends.qemu.backend: QemuCellBackend over FakeQemuRunner.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/backend.py (codingrules section 3). The cross-implementation
    contract (idempotent destroy, capability honouring, concurrency safety, ...) lives in
    packages/hivemind/tests/contracts/test_cell_backend_contract.py instead; this module covers
    QemuCellBackend's own extra surface: cpu-core rounding, the serial-marker readiness wait (and
    its timeout), network-plan wiring, and accelerator pass-through.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.backend for QemuCellBackend, under test.
    - packages/hivemind/tests/contracts/test_cell_backend_contract.py for the shared contract.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.fake import FakeReadinessGate
from hivemind.hive.backends.qemu.backend import QemuBackendConfig, QemuCellBackend
from hivemind.hive.backends.qemu.fake import FakeQemuRunner
from hivemind.hive.errors import CellProvisionError
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


def _make_spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec, with sensible defaults for every field a test ignores."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
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
) -> tuple[QemuCellBackend, FakeQemuRunner, FakeReadinessGate]:
    """Build a fresh QemuCellBackend over fresh fakes, plus the fakes for direct assertions."""
    runner = FakeQemuRunner()
    gate = FakeReadinessGate(clock)
    endpoint = QueenEndpoint(
        waggle_url="ws://localhost:8710",
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex="00" * 32,
    )
    config = QemuBackendConfig(
        base_image=Path("base-ubuntu.qcow2"), vm_root=Path("vm_root"), max_cells=max_cells
    )
    backend = QemuCellBackend(runner, gate, endpoint, clock, config=config)
    return backend, runner, gate


async def test_provision_rounds_fractional_cpu_cores_up_to_a_whole_vcpu() -> None:
    backend, runner, _ = _make_backend(FakeClock())

    await backend.provision(_make_spec(cpu_cores=1.2))

    assert runner.start_vm_calls[0].cpu_cores == 2


async def test_provision_passes_the_probed_accelerator_through() -> None:
    backend, runner, _ = _make_backend(FakeClock())
    runner.set_accelerator("kvm")

    await backend.provision(_make_spec())

    assert runner.start_vm_calls[0].accelerator == "kvm"


async def test_provision_stamps_hive_id_label() -> None:
    backend, runner, _ = _make_backend(FakeClock())
    spec = _make_spec()

    await backend.provision(spec)

    assert runner.start_vm_calls[0].labels["hive_id"] == spec.hive_id


async def test_provision_refuses_vpn_tor_on_any_image_but_night_veil_ubuntu() -> None:
    backend, runner, gate = _make_backend(FakeClock())
    # image defaults away from "night-veil-ubuntu" (_make_spec): the in-guest kill-switch that
    # image lacks is VPN_TOR's only real enforcement, so this must be refused before anything is
    # created, whatever else the spec asks for.
    spec = _make_spec(network_policy=NetworkPolicy.VPN_TOR, comb_shield=CombShieldLevel.NIGHT_VEIL)

    with pytest.raises(CellProvisionError, match="VPN_TOR"):
        await backend.provision(spec)

    assert runner.start_vm_calls == []
    assert gate.expect_calls == []


async def test_provision_accepts_vpn_tor_on_the_night_veil_ubuntu_image() -> None:
    backend, runner, _ = _make_backend(FakeClock())
    spec = _make_spec(
        image="night-veil-ubuntu",
        network_policy=NetworkPolicy.VPN_TOR,
        comb_shield=CombShieldLevel.NIGHT_VEIL,
    )

    cell = await backend.provision(spec)

    assert cell.comb_shield is CombShieldLevel.NIGHT_VEIL
    # QEMU's own SLIRP network gives unrestricted outbound reach (module docstring: the in-guest
    # kill-switch is the real boundary, not QEMU's own network layer).
    assert runner.start_vm_calls[0].netdev_arg == "user,id=net0"


async def test_provision_cleans_up_on_start_vm_failure() -> None:
    backend, runner, gate = _make_backend(FakeClock())
    runner.set_start_vm_failure("qemu-system-x86_64 not found")

    with pytest.raises(CellProvisionError, match="qemu-system-x86_64 not found"):
        await backend.provision(_make_spec())

    assert runner.remove_vm_dir_calls == [runner.write_seed_image_calls[0]]
    assert len(gate.forget_calls) == 1


async def test_provision_never_ready_marker_times_out_and_cleans_up() -> None:
    clock = FakeClock()
    backend, runner, gate = _make_backend(clock)
    spec = _make_spec(ready_timeout_s=5.0)

    # provision() mints its own CellId internally, so this test cannot name it in advance;
    # set_next_marker_missing arms whichever Cell the next start_vm() call registers instead.
    runner.set_next_marker_missing()

    task = asyncio.create_task(backend.provision(spec))
    await asyncio.sleep(0)  # Let provision() reach the readiness poll's first clock.sleep await.
    clock.advance(5.0)  # No real sleeping in tests (codingrules 14.5): drive the fake forward.

    with pytest.raises(CellProvisionError, match="readiness marker"):
        await task

    assert runner.remove_vm_dir_calls == [runner.start_vm_calls[0].cell_id]
    assert len(gate.forget_calls) == 1


async def test_provision_gate_never_ready_times_out_after_the_marker_is_seen() -> None:
    clock = FakeClock()
    backend, _runner, gate = _make_backend(clock)
    spec = _make_spec(ready_timeout_s=5.0)
    gate.set_next_never_ready()

    task = asyncio.create_task(backend.provision(spec))
    await asyncio.sleep(0)  # Let provision() reach gate.wait_ready's own clock.sleep(5.0) await.
    clock.advance(5.0)

    with pytest.raises(CellProvisionError, match="never reported ready"):
        await task


async def test_headroom_exceeded_raises_before_creating_anything() -> None:
    backend, runner, gate = _make_backend(FakeClock(), max_cells=1)
    await backend.provision(_make_spec())

    with pytest.raises(CellProvisionError, match="headroom"):
        await backend.provision(_make_spec())

    assert len(runner.start_vm_calls) == 1
    assert len(gate.expect_calls) == 1


async def test_capabilities_headroom_tracks_active_count() -> None:
    backend, _, _ = _make_backend(FakeClock(), max_cells=2)

    assert backend.capabilities.headroom == 2
    cell = await backend.provision(_make_spec())
    assert backend.capabilities.headroom == 1
    await backend.destroy(cell.id)
    assert backend.capabilities.headroom == 2


async def test_pause_and_resume_delegate_to_the_runner() -> None:
    backend, runner, _ = _make_backend(FakeClock())
    cell = await backend.provision(_make_spec())

    await backend.pause(cell.id)
    await backend.resume(cell.id)

    assert runner.pause_vm_calls == [cell.id]
    assert runner.resume_vm_calls == [cell.id]
