"""Contract suite for CellBackend: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.hive.backends.base.CellBackend contract and runs against every implementation
    registered in `_HARNESSES` below: `hivemind.hive.backends.fake.FakeCellBackend` (roadmap step
    5.2) today. A new CellBackend implementation (Docker, roadmap 5.4; QEMU, 5.11) adds a
    `BackendHarness` here and must pass this suite before it is registered anywhere else
    (codingrules 14.3).

    A harness builds a backend from a Clock and can arrange its *next* provision or destroy call
    to fail, without a test ever checking which backend it got (codingrules section 8.6's
    "capabilities, never name" rule, applied to backends): the pause/resume test reads
    `backend.capabilities.can_pause` instead of branching on the harness's own name.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.base for the CellBackend protocol under test.
    - hivemind.hive.backends.fake for FakeCellBackend, the first implementation registered here.
    - packages/hivemind/tests/contracts/test_real_cell_source_contract.py for the harness-per-
      implementation pattern this suite mirrors, for a different Protocol.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import pytest
from builders.forage import make_capacity

from hivemind.cell import AccessLevel, CellKind
from hivemind.hive.backends.base import CellBackend
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.errors import BackendCapabilityError, CellDestroyError, CellProvisionError
from hivemind.hive.models import VirtualCellSpec
from waggle.clock import Clock, FakeClock
from waggle.ids import CellId, HiveId, new_hive_id


def _make_spec(hive_id: HiveId, **overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec for `hive_id`, with sensible defaults elsewhere."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 2.0,
        "memory_bytes": 2 * 1024**3,
        "disk_bytes": 10 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": hive_id,
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


class BackendHarness(Protocol):
    """Build a CellBackend of one kind, and arrange its next provision/destroy call to fail."""

    def build(self, clock: Clock) -> CellBackend:
        """Build a fresh backend with nothing provisioned yet."""
        ...

    def arrange_provision_failure(self, reason: str) -> None:
        """Make the most recently built backend's next provision() raise CellProvisionError."""
        ...

    def arrange_destroy_failure(self, reason: str) -> None:
        """Make the most recently built backend's next destroy() raise CellDestroyError."""
        ...


class _FakeHarness:
    """Builds a FakeCellBackend and drives its own failure switches."""

    def __init__(self) -> None:
        """Start with no backend built yet; `build()` creates one."""
        self._backend: FakeCellBackend | None = None

    def build(self, clock: Clock) -> CellBackend:
        """Build a fresh FakeCellBackend on `clock`."""
        self._backend = FakeCellBackend(clock)
        return self._backend

    def arrange_provision_failure(self, reason: str) -> None:
        """Arm the built FakeCellBackend's set_provision_failure switch."""
        self._active().set_provision_failure(reason)

    def arrange_destroy_failure(self, reason: str) -> None:
        """Arm the built FakeCellBackend's set_destroy_failure switch."""
        self._active().set_destroy_failure(reason)

    def _active(self) -> FakeCellBackend:
        """Return the built backend, or raise if `build()` was never called."""
        if self._backend is None:
            raise RuntimeError("build() must be called before arranging a failure.")
        return self._backend


_HARNESSES: dict[str, BackendHarness] = {"fake": _FakeHarness()}


@pytest.fixture(params=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> BackendHarness:
    """One BackendHarness per registered CellBackend implementation."""
    return _HARNESSES[request.param]


@pytest.fixture
def backend(harness: BackendHarness) -> CellBackend:
    """A freshly built backend from `harness`, on its own FakeClock."""
    return harness.build(FakeClock())


async def test_provision_returns_a_virtual_full_access_cell(backend: CellBackend) -> None:
    hive_id = new_hive_id(FakeClock())

    cell = await backend.provision(_make_spec(hive_id))

    assert cell.kind is CellKind.VIRTUAL
    assert cell.access_level is AccessLevel.FULL


async def test_failed_provision_leaves_nothing_in_list_cells(
    harness: BackendHarness, backend: CellBackend
) -> None:
    hive_id = new_hive_id(FakeClock())
    harness.arrange_provision_failure("contract test failure")

    with pytest.raises(CellProvisionError):
        await backend.provision(_make_spec(hive_id))

    assert await backend.list_cells(hive_id) == ()


async def test_destroy_is_idempotent(backend: CellBackend) -> None:
    hive_id = new_hive_id(FakeClock())
    cell = await backend.provision(_make_spec(hive_id))

    await backend.destroy(cell.id)
    await backend.destroy(cell.id)  # A second destroy is a silent no-op, not an error.

    assert await backend.list_cells(hive_id) == ()


async def test_destroy_of_an_unknown_id_is_silent(backend: CellBackend) -> None:
    await backend.destroy(CellId("cell_never_provisioned"))  # Must not raise.


async def test_destroy_failure_is_a_typed_error(
    harness: BackendHarness, backend: CellBackend
) -> None:
    hive_id = new_hive_id(FakeClock())
    cell = await backend.provision(_make_spec(hive_id))
    harness.arrange_destroy_failure("contract test failure")

    with pytest.raises(CellDestroyError):
        await backend.destroy(cell.id)


async def test_list_cells_filters_by_hive_id(backend: CellBackend) -> None:
    own_hive_id = new_hive_id(FakeClock())
    other_hive_id = new_hive_id(FakeClock())
    own_cell = await backend.provision(_make_spec(own_hive_id))
    await backend.provision(_make_spec(other_hive_id))

    records = await backend.list_cells(own_hive_id)

    assert [record.cell_id for record in records] == [own_cell.id]


async def test_pause_resume_is_honoured_or_cleanly_refused_per_capabilities(
    backend: CellBackend,
) -> None:
    hive_id = new_hive_id(FakeClock())
    cell = await backend.provision(_make_spec(hive_id))

    # codingrules section 8.6's "capabilities, never name" rule: this test never asks which
    # backend it got, only what it declared it can do.
    if backend.capabilities.can_pause:
        await backend.pause(cell.id)  # Must not raise.
        await backend.resume(cell.id)  # Must not raise.
    else:
        with pytest.raises(BackendCapabilityError):
            await backend.pause(cell.id)
        with pytest.raises(BackendCapabilityError):
            await backend.resume(cell.id)


async def test_concurrent_provisions_are_safe(backend: CellBackend) -> None:
    hive_id = new_hive_id(FakeClock())
    specs = [_make_spec(hive_id, image=f"base-ubuntu-{i}") for i in range(5)]

    cells = await asyncio.gather(*(backend.provision(spec) for spec in specs))

    assert len({cell.id for cell in cells}) == len(cells)  # Every id is unique: no collision.
    records = await backend.list_cells(hive_id)
    assert {record.cell_id for record in records} == {cell.id for cell in cells}
