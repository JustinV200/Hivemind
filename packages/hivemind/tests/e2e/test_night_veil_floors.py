"""End-to-end tests for Night Veil's floors through a running Queen (roadmap steps 10.3a-d).

A human's durable goal request naming NIGHT_VEIL is planned by the Queen on her own tick, placed
on a fresh Virtual Cell, provisioned by a backend whose minted bootstrap dials the Hive Stand's
hidden service through Tor, attested by the Night Veil probe, and assigned: the task is bound to
NIGHT_VEIL and the placement cites the request. A tier a planner set on its own is refused at
placement before anything is provisioned; a Night Veil goal whose plan asks for the cloud
metadata endpoint is refused before anything is persisted. Everything below the Queen is the real
Virtual Cell path (`CellLifecycle`, `QueenReadinessGate`, `LifecycleVirtualCellProvider`) over a
fake backend and a fake attestation probe, as test_virtual_cells_night_veil.py does, but driven by
the Queen's own planning and dispatch instead of calling the provider directly.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - test_virtual_cells_night_veil for the provider-level Night Veil criteria this builds on.
    - hivemind.queen.dispatcher.night_veil and hivemind.guard.policy.floors for what decides.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Sequence

import pytest
from builders.cells import make_identity
from builders.forage import make_capacity
from builders.human import make_goal_request, queen_responder, single_task_plan, wait_until
from builders.queen import make_queen_deps

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.cell import Cell, CombShieldLevel
from hivemind.hive import VirtualCellRecord
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.bootstrap import CellReadyInfo, NightVeilLink, QueenEndpoint
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from hivemind.hive.night_veil import FakeNightVeilProbe
from hivemind.hive.registry import BackendRegistry
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import PheromoneEvent, TrailQuery, TrailRecorder
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.provider import LifecycleVirtualCellProvider
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.intake import GoalRequest, GoalRequestState
from hivemind.queen.placement import NightVeilConstraints, PlacementPolicy, VirtualBackendCandidate
from hivemind.queen.queen import Queen
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import CellId, HiveId, new_node_id, new_warden_id
from waggle.transport.memory import MemoryTransport

pytestmark = pytest.mark.e2e

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion:8710"
_TOR = "socks5h://127.0.0.1:9050"
_DECISION: dict[str, object] = {"action": "RECORD", "reason": "Nothing to decide."}
_NIGHT_VEIL_NEEDS: dict[str, object] = {"comb_shield": "NIGHT_VEIL", "isolation": "REQUIRED"}


class _DiallingBackend:
    """A fake backend whose Cells dial straight back, as a real Cell's Warden does on boot.

    `FakeCellBackend` mints each Cell's bootstrap through the same per-tier endpoint choice every
    real backend makes (`cell_endpoint`); this adds what a real Cell and the Queen's listener do
    next: register the Cell's key, attach its Warden to the Queen, and report it ready.
    """

    def __init__(self, deps: QueenDeps, gate: QueenReadinessGate) -> None:
        endpoint = QueenEndpoint(
            waggle_url="ws://127.0.0.1:8710",
            queen_node_id=deps.identity.node_id,
            queen_verify_key_hex="ab" * 32,
            night_veil=NightVeilLink(waggle_url=f"ws://{_ONION}", socks_proxy_url=_TOR),
        )
        self.delegate = FakeCellBackend(deps.clock, endpoint=endpoint)
        self._deps, self._gate = deps, gate
        self.attach: Callable[[WardenLink], Awaitable[None]] | None = None

    @property
    def name(self) -> str:
        return self.delegate.name

    @property
    def capabilities(self) -> BackendCapabilities:
        return self.delegate.capabilities

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        cell = await self.delegate.provision(spec)
        bootstrap = self.delegate.bootstraps[cell.id]
        await self._gate.expect(cell.id, bootstrap.public_key_hex)
        clock = self._deps.clock
        warden_id = new_warden_id(clock)
        hop = Hop(
            sender=self._deps.identity.hive_id, recipient=warden_id, node_id=new_node_id(clock)
        )
        transport, _cell_side = MemoryTransport.pair(Codec(), Codec())
        assert self.attach is not None
        await self.attach(WardenLink(warden_id=warden_id, cell=cell, transport=transport, hop=hop))
        ready = CellReadyInfo(capabilities=cell.capabilities, capacity=cell.capacity)
        self._gate.resolve(cell.id, new_node_id(clock), ready)
        return cell

    async def destroy(self, cell_id: CellId) -> None:
        await self.delegate.destroy(cell_id)

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        return await self.delegate.list_cells(hive_id)

    async def pause(self, cell_id: CellId) -> None:
        await self.delegate.pause(cell_id)

    async def resume(self, cell_id: CellId) -> None:
        await self.delegate.resume(cell_id)


def _hive(
    build_plan: Callable[[str], dict[str, object]],
) -> tuple[Queen, QueenDeps, _DiallingBackend]:
    """A Queen with a Night Veil profile, a fake Virtual side and an always-green probe."""
    provider = FakeLLMProvider(responder=queen_responder(_DECISION, [], build_plan))
    base, _link, _end = make_queen_deps(fake_provider=provider)
    gate = QueenReadinessGate()
    backend = _DiallingBackend(base, gate)
    registry = BackendRegistry()
    registry.register(backend.name, lambda: backend)
    identity = make_identity(base.clock)
    lifecycle = CellLifecycle(registry, base.trail, base.clock, identity)
    recorder = TrailRecorder(
        trail=base.trail, clock=base.clock, hive_id=identity.hive_id, node_id=identity.node_id
    )
    cells = LifecycleVirtualCellProvider(lifecycle, gate, recorder, lambda _c: FakeNightVeilProbe())
    deps = dataclasses.replace(
        base,
        virtual_provider=cells,
        virtual_backends=(_candidate(base, backend),),
        placement_policy=PlacementPolicy(prefer="virtual", night_veil=_profile()),
        in_process_providers=frozenset({"fake"}),
    )
    queen = Queen(deps)
    cells.bind_queen(queen)
    backend.attach = queen.attach_warden
    return queen, deps, backend


def _profile() -> NightVeilConstraints:
    """The Night Veil tier profile an operator configured: a hidden service and a Tor proxy."""
    return NightVeilConstraints(
        required_network_policy=NetworkPolicy.VPN_TOR,
        hive_stand_onion_address=_ONION,
        socks_proxy_url=_TOR,
        locale_profile="C.UTF-8",
    )


def _candidate(deps: QueenDeps, backend: _DiallingBackend) -> VirtualBackendCandidate:
    """The fake backend as placement sees it, listing the Night Veil image."""
    spec = VirtualCellSpec(
        image="night-veil-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.EGRESS_ONLY,  # Placement re-stamps VPN_TOR for the tier.
        capacity=make_capacity(),
        hive_id=deps.identity.hive_id,
        ready_timeout_s=5.0,
    )
    return VirtualBackendCandidate(
        name=backend.name, capabilities=backend.capabilities, specs=(spec,)
    )


def _night_veil_plan(needs: dict[str, object]) -> Callable[[str], dict[str, object]]:
    """A one-task plan whose task has `needs`."""

    def build(goal: str) -> dict[str, object]:
        plan = single_task_plan(goal)
        tasks = plan["tasks"]
        assert isinstance(tasks, list)
        tasks[0]["needs"] = needs
        return plan

    return build


async def _run_until(queen: Queen, done: Callable[[], Awaitable[bool]]) -> None:
    """Run the Queen's own loop until `done()`, then stop her cleanly."""
    loop = asyncio.ensure_future(queen.run())
    try:
        await wait_until(done)
    finally:
        await queen.stop()
        await asyncio.wait_for(loop, timeout=5.0)


async def _tasks(deps: QueenDeps) -> tuple[Task, ...]:
    return tuple(await deps.chamber.list(TaskFilter()))


async def _events(deps: QueenDeps, kind: str) -> list[PheromoneEvent]:
    return list(await deps.trail.query(TrailQuery(kind=kind)))


async def _settled(deps: QueenDeps, request: GoalRequest) -> bool:
    row = await deps.goal_requests.get(request.id)
    return row.state in {GoalRequestState.PLANNED, GoalRequestState.REFUSED}


async def test_a_human_night_veil_request_runs_on_an_attested_cell_dialling_tor() -> None:
    queen, deps, backend = _hive(single_task_plan)
    request = make_goal_request(deps.clock, comb_shield=CombShieldLevel.NIGHT_VEIL)

    await queen.request_goal(request)

    async def running() -> bool:
        tasks = await _tasks(deps)
        return bool(tasks) and tasks[0].status is TaskStatus.RUNNING

    await _run_until(queen, running)
    [task] = await _tasks(deps)
    assert task.bound_tier is CombShieldLevel.NIGHT_VEIL
    assert task.spec.goal_request_id == request.id
    [spec] = backend.delegate.provision_calls
    assert spec.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert spec.network_policy is NetworkPolicy.VPN_TOR
    [bootstrap] = backend.delegate.bootstraps.values()
    environment = bootstrap.environment()
    assert environment["HIVEMIND_QUEEN_WAGGLE_URL"] == f"ws://{_ONION}"
    assert environment["HIVEMIND_SOCKS_PROXY_URL"] == _TOR
    [attested] = await _events(deps, "cell.attested")
    assert attested.payload["passed"] is True
    [placed] = await _events(deps, "queen.placed")
    assert placed.payload["goal_request_id"] == request.id
    assert await _events(deps, "guard.denied") == []


async def test_a_night_veil_tier_the_planner_set_on_its_own_never_provisions_a_cell() -> None:
    queen, deps, backend = _hive(_night_veil_plan(_NIGHT_VEIL_NEEDS))
    request = make_goal_request(deps.clock)  # The human named no tier at all.

    await queen.request_goal(request)

    async def cancelled() -> bool:
        tasks = await _tasks(deps)
        return bool(tasks) and tasks[0].status is TaskStatus.CANCELLED

    await _run_until(queen, cancelled)
    assert backend.delegate.provision_calls == []
    [denied] = await _events(deps, "guard.denied")
    assert denied.payload["rule"] == "guard.tier_floor.night_veil_initiation"


async def test_a_night_veil_goal_asking_for_the_metadata_endpoint_is_refused_unplanned() -> None:
    plan = _night_veil_plan({"network_scopes": ["169.254.169.254"]})
    queen, deps, backend = _hive(plan)
    request = make_goal_request(deps.clock, comb_shield=CombShieldLevel.NIGHT_VEIL)

    await queen.request_goal(request)
    await _run_until(queen, lambda: _settled(deps, request))

    refused = await deps.goal_requests.get(request.id)
    assert refused.state is GoalRequestState.REFUSED
    assert await _tasks(deps) == ()
    assert backend.delegate.provision_calls == []
    [denied] = await _events(deps, "guard.denied")
    assert denied.payload["rule"] == "guard.tier_floor.night_veil_location"
