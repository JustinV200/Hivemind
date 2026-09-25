"""End-to-end tests for Virtual Cells: Night Veil, at the provider level.

`.claude/roadmap.md` lines 1030-1038, criterion (h). Split out of `test_virtual_cells.py` (see
that module's own docstring for the rest of the phase 5 exit criteria) purely to keep both files
under codingrules section 5.1's 400-LOC hard limit -- mirrors the existing
`test_phase4_exit_criteria.py` / `test_phase4_exit_criteria_forage.py` split.

`hivemind.cli.compose.virtual_cells`'s own `probe_factory` is fail-closed for a real `hive run`
(that module's own docstring); this suite proves the same contract `LifecycleVirtualCellProvider`
itself upholds instead, mirroring `tests/unit/queen/cell_gate/test_provider.py`'s own shape. The
same path driven by the Queen's own planning and dispatch, now that the composition root builds
`PlacementPolicy.night_veil` (roadmap step 10.3a), is `test_night_veil_floors.py`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md lines 1030-1038 for the exit criteria this module proves.
    - test_virtual_cells for the rest of the phase 5 exit criteria (a-g).
    - tests/unit/queen/cell_gate/test_provider.py for the shape this module's helpers mirror.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from builders.cells import make_identity

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CombShieldLevel
from hivemind.hive import CellProvisionError, VirtualCellRecord
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.bootstrap import CellReadyInfo
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.lifecycle import CellLifecycle, OverwinterSettings
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from hivemind.hive.night_veil import CheckResult, CheckStatus, FakeNightVeilProbe
from hivemind.hive.overwinter.policy import OverwinterConfig, OverwinterDecision, ReleaseOutcome
from hivemind.hive.overwinter.pool import OverwinterPool
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone import PheromoneTrail, TrailQuery, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.provider import (
    LifecycleVirtualCellProvider,
    NightVeilProbeFactory,
)
from hivemind.queen.deps import WardenLink
from hivemind.queen.placement import ProvisionVirtual
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import CellId, HiveId, new_grant_id, new_hive_id, new_node_id, new_warden_id
from waggle.transport.memory import MemoryTransport

pytestmark = pytest.mark.e2e


def _night_veil_spec(**overrides: object) -> VirtualCellSpec:
    fields: dict[str, object] = {
        "image": "night-veil-ubuntu",
        "cpu_cores": 1.0,
        "memory_bytes": 1024**3,
        "disk_bytes": 8 * 1024**3,
        "network_policy": NetworkPolicy.VPN_TOR,
        "comb_shield": CombShieldLevel.NIGHT_VEIL,
        "capacity": _capacity(),
        "hive_id": new_hive_id(FakeClock()),
        "ready_timeout_s": 5.0,
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _capacity() -> object:
    from hivemind.forage import ForageCapacity, HostCapacity
    from waggle.messages import OsFamily as WireOsFamily

    return ForageCapacity(
        host=HostCapacity(
            cores=2,
            memory_bytes=2 * 1024**3,
            memory_free_bytes=2 * 1024**3,
            disk_bytes=10 * 1024**3,
            disk_free_bytes=10 * 1024**3,
            cpu_load=0.0,
            gpus=(),
            arch="x86_64",
            os=WireOsFamily.LINUX,
        ),
        local_seats=(),
        max_sub_bees=4,
    )


class _GatedFakeCellBackend:
    """A minimal stand-in for a real backend + CellListener, mirroring test_provider.py's own.

    `hivemind.hive.backends.fake.FakeCellBackend` never dials a `QueenReadinessGate` itself (no
    real container, no real listener); this collapses "the backend's own `expect`" and "the
    listener's own attach-then-resolve" into one synchronous `provision()` override, since this
    suite is proving `LifecycleVirtualCellProvider`'s own acquire/attest logic, not the real
    concurrent handshake (already covered by `tests/unit/queen/cell_gate/test_listener.py`).
    """

    def __init__(
        self, clock: FakeClock, gate: QueenReadinessGate, wardens: list[WardenLink]
    ) -> None:
        self._delegate = FakeCellBackend(
            clock,
            capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None),
        )
        self._clock = clock
        self._gate = gate
        self._wardens = wardens

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._delegate.capabilities

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        cell = await self._delegate.provision(spec)
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

    async def destroy(self, cell_id: CellId) -> None:
        await self._delegate.destroy(cell_id)

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        return await self._delegate.list_cells(hive_id)

    async def pause(self, cell_id: CellId) -> None:
        await self._delegate.pause(cell_id)

    async def resume(self, cell_id: CellId) -> None:
        await self._delegate.resume(cell_id)


class _StubQueen:
    """A minimal stand-in for `hivemind.queen.queen.Queen`: only `.wardens` is read."""

    def __init__(self, wardens: list[WardenLink]) -> None:
        self._wardens = wardens

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        return tuple(self._wardens)


def _night_veil_provider(
    *, with_pool: bool = False, probe_factory: NightVeilProbeFactory | None = None
) -> tuple[LifecycleVirtualCellProvider, CellLifecycle, list[WardenLink], PheromoneTrail]:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = BackendRegistry()
    gate = QueenReadinessGate()
    wardens: list[WardenLink] = []
    backend = _GatedFakeCellBackend(clock, gate, wardens)
    registry.register("fake", lambda: backend)
    overwinter_config = OverwinterConfig(
        enabled=True, max_cells=10, max_per_image=10, max_dormant_s=3600.0, disk_budget_mb=1024**3
    )
    overwinter = (
        OverwinterSettings(pool=OverwinterPool(clock, overwinter_config), config=overwinter_config)
        if with_pool
        else None
    )
    identity = make_identity(clock)
    lifecycle = CellLifecycle(registry, trail, clock, identity, overwinter=overwinter)
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=identity.hive_id, node_id=identity.node_id
    )
    always_green: NightVeilProbeFactory = lambda _cell: FakeNightVeilProbe()  # noqa: E731
    provider = LifecycleVirtualCellProvider(
        lifecycle, gate, recorder, probe_factory if probe_factory is not None else always_green
    )
    provider.bind_queen(_StubQueen(wardens))
    return provider, lifecycle, wardens, trail


def _fake_task() -> Task:
    """A Task stand-in; `LifecycleVirtualCellProvider.acquire` never reads it."""
    return None  # type: ignore[return-value]


async def test_h_night_veil_all_green_attests_and_proceeds() -> None:
    provider, lifecycle, wardens, trail = _night_veil_provider()
    placement = ProvisionVirtual(spec=_night_veil_spec(), backend="fake", reason="test")

    link = await provider.acquire(placement, _fake_task())

    assert link in wardens
    assert lifecycle.status_of(link.cell.id) is VirtualCellStatus.READY
    attested = await trail.query(TrailQuery(kind="cell.attested"))
    assert len(attested) == 1
    assert attested[0].payload["passed"] is True


async def test_h_night_veil_one_red_check_records_fail_and_raises_naming_it() -> None:
    def _red_probe(cell: Cell) -> FakeNightVeilProbe:
        del cell
        return FakeNightVeilProbe(
            overrides={
                "tor_healthy": CheckResult(status=CheckStatus.FAIL, detail="tor.service is down.")
            }
        )

    provider, _lifecycle, _wardens, trail = _night_veil_provider(probe_factory=_red_probe)
    placement = ProvisionVirtual(spec=_night_veil_spec(), backend="fake", reason="test")

    with pytest.raises(CellProvisionError, match="tor_healthy"):
        await provider.acquire(placement, _fake_task())

    attested = await trail.query(TrailQuery(kind="cell.attested"))
    assert len(attested) == 1
    assert attested[0].payload["passed"] is False
    tor_healthy = attested[0].payload["tor_healthy"]
    assert isinstance(tor_healthy, dict)
    assert tor_healthy["status"] == "FAIL"


async def test_h_night_veil_a_red_check_tears_down_the_cell() -> None:
    def _red_probe(cell: Cell) -> FakeNightVeilProbe:
        del cell
        return FakeNightVeilProbe(
            overrides={
                "tor_healthy": CheckResult(status=CheckStatus.FAIL, detail="tor.service is down.")
            }
        )

    provider, lifecycle, _wardens, trail = _night_veil_provider(probe_factory=_red_probe)
    placement = ProvisionVirtual(spec=_night_veil_spec(), backend="fake", reason="test")

    with pytest.raises(CellProvisionError):
        await provider.acquire(placement, _fake_task())

    kinds = [e.kind for e in await trail.query(TrailQuery())]
    assert "cell.destroyed" in kinds
    del lifecycle  # unused past the acquire call; kept for readability with the other (h) tests.


async def test_h_night_veil_never_overwinters_even_with_the_pool_enabled() -> None:
    """`release()` short-circuits to TEARDOWN for a NIGHT_VEIL Cell, pool or no pool."""
    provider, lifecycle, _wardens, _trail = _night_veil_provider(with_pool=True)
    placement = ProvisionVirtual(spec=_night_veil_spec(), backend="fake", reason="test")
    link = await provider.acquire(placement, _fake_task())
    cell_id = link.cell.id
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

    assert decision is OverwinterDecision.TEARDOWN
