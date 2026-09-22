"""Unit tests for hivemind.queen.cell_gate.provider: LifecycleVirtualCellProvider.

`FakeCellBackend` never dials a `QueenReadinessGate` the way a real `CellBackend`/`CellListener`
pair does (its own module docstring: it is a bare in-memory Cell registry, no readiness seam), so
`_GatedFakeCellBackend` below stands in for both halves at once -- the backend's own `expect`/
readiness resolve *and* the listener's own "attach the WardenLink before resolving the gate" --
inside one `provision()` override. This tests `LifecycleVirtualCellProvider`'s own acquire logic
in isolation; the real concurrent handshake (`CellListener` racing a dialling Cell) is already
covered by `tests/unit/queen/cell_gate/test_listener.py`.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/cell_gate/provider.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.provider for LifecycleVirtualCellProvider, the class under test.
"""

from __future__ import annotations

import pytest
from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.brood_chamber import Task
from hivemind.cell import Cell
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.bootstrap import CellReadyInfo
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import CellProvisionError
from hivemind.hive.lifecycle import CellLifecycle, OverwinterSettings
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import OverwinterConfig, OverwinterDecision, ReleaseOutcome
from hivemind.hive.overwinter.pool import OverwinterPool
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.provider import LifecycleVirtualCellProvider
from hivemind.queen.deps import WardenLink
from hivemind.queen.placement import ProvisionVirtual, ReuseDormant
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_grant_id, new_hive_id, new_node_id, new_warden_id
from waggle.transport.memory import MemoryTransport


async def _no_op_scrub(cell: object) -> None:
    """A Scrubber that does nothing."""


class _GatedFakeCellBackend(FakeCellBackend):
    """A FakeCellBackend that also plays the real backend's + CellListener's own roles.

    `provision()` mints the Cell as usual, then registers and resolves it on `gate` and appends a
    fresh `WardenLink` to `wardens` -- exactly what a real backend (`expect`) plus a real
    `CellListener` (attach, then `resolve`) do together, collapsed into one synchronous step since
    this test is not exercising that concurrency itself (module docstring).
    """

    def __init__(
        self, clock: FakeClock, gate: QueenReadinessGate, wardens: list[WardenLink]
    ) -> None:
        super().__init__(
            clock,
            capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None),
        )
        self._gate = gate
        self._wardens = wardens

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        cell = await super().provision(spec)
        await self._gate.expect(cell.id, "deadbeef")
        link = WardenLink(
            warden_id=new_warden_id(self._clock),
            cell=cell,
            transport=MemoryTransport.pair(Codec(), Codec())[0],
            hop=Hop(sender="hive_x", recipient="warden_x", node_id=new_node_id(self._clock)),
        )
        self._wardens.append(link)
        self._gate.resolve(
            cell.id,
            new_node_id(self._clock),
            CellReadyInfo(capabilities=cell.capabilities, capacity=cell.capacity),
        )
        return cell


class _StubQueen:
    """A minimal stand-in for hivemind.queen.queen.Queen: only `.wardens` is read."""

    def __init__(self, wardens: list[WardenLink]) -> None:
        self._wardens = wardens

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        return tuple(self._wardens)


def _spec(**overrides: object) -> VirtualCellSpec:
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 1.0,
        "memory_bytes": 1024**3,
        "disk_bytes": 8 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
        "ready_timeout_s": 5.0,
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _overwinter_config() -> OverwinterConfig:
    return OverwinterConfig(
        enabled=True, max_cells=10, max_per_image=10, max_dormant_s=3600.0, disk_budget_mb=1024**3
    )


def _fake_task() -> Task:
    """A Task stand-in; LifecycleVirtualCellProvider.acquire never reads it today."""
    return None  # type: ignore[return-value]


def _build(
    gated: bool = True, with_pool: bool = False
) -> tuple[LifecycleVirtualCellProvider, CellLifecycle, list[WardenLink], QueenReadinessGate]:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = BackendRegistry()
    gate = QueenReadinessGate()
    wardens: list[WardenLink] = []
    if gated:
        backend: FakeCellBackend = _GatedFakeCellBackend(clock, gate, wardens)
    else:
        backend = FakeCellBackend(
            clock,
            capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None),
        )
    registry.register("fake", lambda: backend)
    overwinter = (
        OverwinterSettings(
            pool=OverwinterPool(clock, _overwinter_config()), config=_overwinter_config()
        )
        if with_pool
        else None
    )
    lifecycle = CellLifecycle(registry, trail, clock, make_identity(clock), overwinter=overwinter)
    provider = LifecycleVirtualCellProvider(lifecycle, gate)
    provider.bind_queen(_StubQueen(wardens))
    return provider, lifecycle, wardens, gate


async def test_acquire_provisions_and_returns_the_attached_link() -> None:
    provider, lifecycle, wardens, _gate = _build()
    placement = ProvisionVirtual(spec=_spec(), backend="fake", reason="test")

    link = await provider.acquire(placement, _fake_task())

    assert link in wardens
    assert lifecycle.status_of(link.cell.id) is VirtualCellStatus.READY


async def test_acquire_provision_never_ready_raises_and_never_returns_a_link() -> None:
    provider, lifecycle, _wardens, _gate = _build(gated=False)
    # The plain FakeCellBackend never touches the gate at all (module docstring): a real backend's
    # own provision() would already have raised CellProvisionError internally before ever getting
    # here (hivemind.hive.lifecycle's own documented contract), so this simulates a backend that
    # (unrealistically) returns before its Cell is actually reachable, to exercise the failure path.
    placement = ProvisionVirtual(spec=_spec(ready_timeout_s=0.01), backend="fake", reason="test")

    with pytest.raises(CellProvisionError):
        await provider.acquire(placement, _fake_task())

    # teardown() cannot move a still-PROVISIONING record straight to DESTROYING (no such edge in
    # hivemind.hive.cell_state.TRANSITIONS -- provider.py's own _teardown_best_effort swallows
    # that and moves on): the record is left tracked, PROVISIONING, for a future sweep enhancement
    # to find (documented gap; a real backend never reaches this branch in the first place).
    tracked = lifecycle.live_cells()
    assert len(tracked) == 1
    assert tracked[0].status is VirtualCellStatus.PROVISIONING


async def test_acquire_reuse_dormant_finds_the_still_attached_link() -> None:
    """A resumed Cell whose connection stayed open (Docker-style pause) needs no fresh handshake."""
    provider, lifecycle, _wardens, _gate = _build(with_pool=True)
    placement = ProvisionVirtual(spec=_spec(), backend="fake", reason="test")
    provisioned = await provider.acquire(placement, _fake_task())
    cell_id = provisioned.cell.id
    await lifecycle.grant(cell_id, new_grant_id(FakeClock()))
    decision = await lifecycle.release(
        cell_id,
        ReleaseOutcome(
            rolled_back_whole_cell=False,
            has_block_wax=False,
            single_use=False,
            backend_can_pause=True,
        ),
    )
    assert decision is OverwinterDecision.OVERWINTER
    await lifecycle.overwinter(cell_id, scrub=_no_op_scrub)
    assert lifecycle.status_of(cell_id) is VirtualCellStatus.DORMANT

    dormant = ReuseDormant(cell_id=cell_id, warden_id=provisioned.warden_id, reason="test")
    link = await provider.acquire(dormant, _fake_task())

    assert link is provisioned  # The connection (module docstring) never left `wardens`.
    assert lifecycle.status_of(cell_id) is VirtualCellStatus.READY


async def test_acquire_reuse_dormant_tears_down_when_no_link_is_found() -> None:
    """A resumed Cell whose connection did NOT survive the pause: no fallback link, so it fails."""
    provider, lifecycle, wardens, _gate = _build(with_pool=True)
    placement = ProvisionVirtual(spec=_spec(), backend="fake", reason="test")
    provisioned = await provider.acquire(placement, _fake_task())
    cell_id = provisioned.cell.id
    await lifecycle.grant(cell_id, new_grant_id(FakeClock()))
    await lifecycle.release(
        cell_id,
        ReleaseOutcome(
            rolled_back_whole_cell=False,
            has_block_wax=False,
            single_use=False,
            backend_can_pause=True,
        ),
    )
    await lifecycle.overwinter(cell_id, scrub=_no_op_scrub)
    wardens.remove(provisioned)  # Simulate the pause closing the connection.

    dormant = ReuseDormant(cell_id=cell_id, warden_id=None, reason="test")
    with pytest.raises(CellProvisionError):
        await provider.acquire(dormant, _fake_task())

    assert lifecycle.status_of(cell_id) is None  # Torn down after the failed resume.
