"""Unit tests for hivemind.hive.backends.fake: FakeCellBackend and FakeReadinessGate.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/backends/fake.py
    (codingrules section 3). The cross-implementation contract (idempotent destroy, capability
    honouring, concurrency safety, ...) lives in
    packages/hivemind/tests/contracts/test_cell_backend_contract.py instead; this module covers
    each fake's own extra surface: its failure/delay switches and call recording.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.fake for FakeCellBackend and FakeReadinessGate, the classes under
      test.
    - packages/hivemind/tests/contracts/test_cell_backend_contract.py for the shared CellBackend
      contract.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity

from hivemind.cell import AccessLevel, CellKind
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.bootstrap import CellReadyInfo, QueenEndpoint
from hivemind.hive.backends.fake import FakeCellBackend, FakeReadinessGate
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import BackendCapabilityError, CellDestroyError, CellProvisionError
from hivemind.hive.models import VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_hive_id, new_node_id


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


async def test_provision_returns_a_virtual_full_access_cell() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()

    cell = await backend.provision(spec)

    assert cell.kind is CellKind.VIRTUAL
    assert cell.access_level is AccessLevel.FULL
    assert cell.comb_shield is spec.comb_shield
    assert cell.source == "fake"


async def test_provision_records_the_spec() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()

    await backend.provision(spec)

    assert backend.provision_calls == [spec]


@pytest.mark.parametrize("exoskeleton", [True, False])
async def test_provision_capabilities_follow_the_exoskeleton_flag(exoskeleton: bool) -> None:
    backend = FakeCellBackend(FakeClock())

    cell = await backend.provision(_make_spec(exoskeleton=exoskeleton))

    assert cell.capabilities.has_display is exoskeleton
    assert cell.capabilities.has_audio is exoskeleton
    assert cell.capabilities.can_start_display is exoskeleton


def _endpoint() -> QueenEndpoint:
    clock = FakeClock()
    return QueenEndpoint(
        waggle_url="ws://127.0.0.1:0",
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex="ab" * 32,
    )


async def test_provision_with_no_endpoint_mints_no_bootstrap() -> None:
    backend = FakeCellBackend(FakeClock())

    cell = await backend.provision(_make_spec())

    assert backend.bootstraps == {}
    assert cell.id not in backend.bootstraps


async def test_provision_with_an_endpoint_mints_a_real_bootstrap_for_the_cell() -> None:
    backend = FakeCellBackend(FakeClock(), endpoint=_endpoint())
    spec = _make_spec()

    cell = await backend.provision(spec)

    assert cell.id in backend.bootstraps
    bootstrap = backend.bootstraps[cell.id]
    assert bootstrap.cell_id == cell.id
    assert bootstrap.hive_id == spec.hive_id
    # environment() renders the exact HIVEMIND_* variables a real container would get.
    env = bootstrap.environment()
    assert env["HIVEMIND_CELL_ID"] == cell.id
    assert env["HIVEMIND_HIVE_ID"] == spec.hive_id


async def test_provision_with_an_endpoint_mints_a_fresh_bootstrap_per_cell() -> None:
    backend = FakeCellBackend(FakeClock(), endpoint=_endpoint())

    first = await backend.provision(_make_spec())
    second = await backend.provision(_make_spec())

    assert len(backend.bootstraps) == 2
    assert (
        backend.bootstraps[first.id].public_key_hex != backend.bootstraps[second.id].public_key_hex
    )


async def test_provisioned_cell_is_listed_under_its_hive_id() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()

    cell = await backend.provision(spec)
    records = await backend.list_cells(spec.hive_id)

    assert [record.cell_id for record in records] == [cell.id]
    assert records[0].labels["hive_id"] == spec.hive_id
    assert records[0].status is VirtualCellStatus.READY


async def test_list_cells_excludes_cells_from_another_hive() -> None:
    backend = FakeCellBackend(FakeClock())
    await backend.provision(_make_spec())
    other_hive_id = new_hive_id(FakeClock())

    records = await backend.list_cells(other_hive_id)

    assert records == ()


async def test_stamped_hive_id_wins_over_a_colliding_spec_label() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec(labels={"hive_id": "not-the-real-one"})

    await backend.provision(spec)
    records = await backend.list_cells(spec.hive_id)

    assert records[0].labels["hive_id"] == spec.hive_id


async def test_destroy_unknown_id_is_a_silent_no_op() -> None:
    backend = FakeCellBackend(FakeClock())
    unknown_id = CellId("cell_does_not_exist")

    await backend.destroy(unknown_id)  # must not raise

    assert backend.destroy_calls == [unknown_id]


async def test_destroy_removes_the_cell_from_list_cells() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()
    cell = await backend.provision(spec)

    await backend.destroy(cell.id)

    assert await backend.list_cells(spec.hive_id) == ()


async def test_set_provision_failure_raises_and_leaves_nothing_listed() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()
    backend.set_provision_failure("simulated outage")

    with pytest.raises(CellProvisionError, match="simulated outage"):
        await backend.provision(spec)

    assert await backend.list_cells(spec.hive_id) == ()


async def test_set_provision_failure_off_lets_provision_succeed_again() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()
    backend.set_provision_failure("simulated outage")
    backend.set_provision_failure(None)

    cell = await backend.provision(spec)

    assert cell.kind is CellKind.VIRTUAL


async def test_set_destroy_failure_raises_and_keeps_the_record() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()
    cell = await backend.provision(spec)
    backend.set_destroy_failure("volume busy")

    with pytest.raises(CellDestroyError, match="volume busy"):
        await backend.destroy(cell.id)

    assert [record.cell_id for record in await backend.list_cells(spec.hive_id)] == [cell.id]


async def test_provision_delay_past_ready_timeout_raises_provision_error() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    spec = _make_spec(ready_timeout_s=5.0)
    backend.set_provision_delay(10.0)

    task = asyncio.create_task(backend.provision(spec))
    await asyncio.sleep(0)  # Let provision() reach its clock.sleep(10.0) await.
    clock.advance(10.0)  # No real sleeping in tests (codingrules 14.5): drive the fake forward.

    with pytest.raises(CellProvisionError, match="did not become reachable"):
        await task


async def test_provision_delay_within_ready_timeout_succeeds() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    spec = _make_spec(ready_timeout_s=30.0)
    backend.set_provision_delay(5.0)

    task = asyncio.create_task(backend.provision(spec))
    await asyncio.sleep(0)  # Let provision() reach its clock.sleep(5.0) await.
    clock.advance(5.0)

    cell = await task
    assert cell.kind is CellKind.VIRTUAL


async def test_set_provision_delay_rejects_negative_values() -> None:
    backend = FakeCellBackend(FakeClock())

    with pytest.raises(ValueError, match="delay_s"):
        backend.set_provision_delay(-1.0)


async def test_pause_and_resume_update_status_in_list_cells() -> None:
    backend = FakeCellBackend(FakeClock())
    spec = _make_spec()
    cell = await backend.provision(spec)

    await backend.pause(cell.id)
    paused = await backend.list_cells(spec.hive_id)
    assert paused[0].status is VirtualCellStatus.DORMANT

    await backend.resume(cell.id)
    resumed = await backend.list_cells(spec.hive_id)
    assert resumed[0].status is VirtualCellStatus.READY

    assert backend.pause_calls == [cell.id]
    assert backend.resume_calls == [cell.id]


async def test_pause_without_can_pause_capability_raises() -> None:
    backend = FakeCellBackend(
        FakeClock(), capabilities=BackendCapabilities(can_snapshot=False, can_pause=False)
    )
    spec = _make_spec()
    cell = await backend.provision(spec)

    with pytest.raises(BackendCapabilityError, match="pause"):
        await backend.pause(cell.id)


async def test_resume_without_can_pause_capability_raises() -> None:
    backend = FakeCellBackend(
        FakeClock(), capabilities=BackendCapabilities(can_snapshot=False, can_pause=False)
    )
    spec = _make_spec()
    cell = await backend.provision(spec)

    with pytest.raises(BackendCapabilityError, match="pause"):
        await backend.resume(cell.id)


async def test_provision_over_headroom_raises_provision_error() -> None:
    backend = FakeCellBackend(
        FakeClock(),
        capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=1),
    )
    await backend.provision(_make_spec())

    with pytest.raises(CellProvisionError, match="headroom"):
        await backend.provision(_make_spec())


# ──────────────────────────────────────────────────────────────────────────────
# FakeReadinessGate
# ──────────────────────────────────────────────────────────────────────────────


async def test_wait_ready_returns_a_default_info_with_no_arrangement() -> None:
    gate = FakeReadinessGate(FakeClock())
    cell_id = new_cell_id(FakeClock())

    info = await gate.wait_ready(cell_id, timeout_s=30.0)

    assert info.capabilities.can_host_model is True
    assert info.capacity.max_sub_bees > 0


async def test_wait_ready_returns_arranged_info() -> None:
    gate = FakeReadinessGate(FakeClock())
    cell_id = new_cell_id(FakeClock())
    arranged = CellReadyInfo(
        capabilities=make_capabilities(has_display=True), capacity=make_capacity()
    )
    gate.set_ready_info(cell_id, arranged)

    info = await gate.wait_ready(cell_id, timeout_s=30.0)

    assert info is arranged


async def test_expect_and_forget_are_recorded() -> None:
    gate = FakeReadinessGate(FakeClock())
    cell_id = new_cell_id(FakeClock())

    await gate.expect(cell_id, "ab" * 32)
    await gate.forget(cell_id)

    assert gate.expect_calls == [cell_id]
    assert gate.forget_calls == [cell_id]


async def test_forget_of_a_never_expected_cell_is_a_silent_no_op() -> None:
    gate = FakeReadinessGate(FakeClock())
    unknown_id = CellId("cell_never_expected")

    await gate.forget(unknown_id)  # must not raise

    assert gate.forget_calls == [unknown_id]


async def test_set_never_ready_makes_wait_ready_time_out() -> None:
    clock = FakeClock()
    gate = FakeReadinessGate(clock)
    cell_id = new_cell_id(clock)
    gate.set_never_ready(cell_id)

    task = asyncio.create_task(gate.wait_ready(cell_id, timeout_s=5.0))
    await asyncio.sleep(0)  # Let wait_ready reach its clock.sleep(5.0) await.
    clock.advance(5.0)  # No real sleeping in tests (codingrules 14.5): drive the fake forward.

    with pytest.raises(TimeoutError, match="never reported ready"):
        await task


async def test_set_never_ready_off_lets_wait_ready_succeed_again() -> None:
    clock = FakeClock()
    gate = FakeReadinessGate(clock)
    cell_id = new_cell_id(clock)
    gate.set_never_ready(cell_id)
    gate.set_never_ready(cell_id, False)

    info = await gate.wait_ready(cell_id, timeout_s=5.0)

    assert info.capabilities.can_host_model is True


async def test_set_next_never_ready_arms_whichever_cell_expect_registers_next() -> None:
    clock = FakeClock()
    gate = FakeReadinessGate(clock)
    already_expected = new_cell_id(clock)
    await gate.expect(already_expected, "ab" * 32)
    gate.set_next_never_ready()
    next_cell = new_cell_id(clock)

    await gate.expect(next_cell, "cd" * 32)

    # The Cell expected before the switch was armed is unaffected; only the next one is.
    assert (await gate.wait_ready(already_expected, timeout_s=1.0)).capabilities.can_host_model

    task = asyncio.create_task(gate.wait_ready(next_cell, timeout_s=1.0))
    await asyncio.sleep(0)
    clock.advance(1.0)
    with pytest.raises(TimeoutError):
        await task


async def test_forget_clears_never_ready_and_arranged_info() -> None:
    clock = FakeClock()
    gate = FakeReadinessGate(clock)
    cell_id = new_cell_id(clock)
    gate.set_never_ready(cell_id)

    await gate.forget(cell_id)

    # A forgotten Cell is back to the default, ready-immediately behaviour: the never-ready switch
    # does not survive a forget(), matching how a backend re-expects a Cell from scratch.
    info = await gate.wait_ready(cell_id, timeout_s=5.0)
    assert info.capabilities.can_host_model is True
