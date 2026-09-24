"""Tests for hivemind.queen.queen.Queen dispatching a ready task onto a Virtual Cell.

Roadmap step 5.7 (ADR-0028): the sibling of test_queen_dispatch.py's own Real-Cell-only dispatch
tests, exercising the new `hivemind.queen.dispatcher.acquire.resolve_link` seam with a fake
`hivemind.queen.deps.VirtualCellProvider` end to end, through `Queen.submit_goal`.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ (codingrules section 3); split out from
    test_queen_dispatch.py (codingrules 14.2) because a Virtual dispatch needs its own fake
    provider and a second Waggle link to pump.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.queen, .dispatcher for the modules under test.
    - hivemind.queen.dispatcher.acquire for resolve_link, the seam this module's fake provider
      stands in for.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from builders.cells import make_cell
from builders.forage import make_capacity
from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import CellKind, CombShieldLevel, HoneyClearance
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import PheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.placement import Placement, PlacementPolicy, VirtualBackendCandidate
from hivemind.queen.queen import Queen
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_warden_id
from waggle.transport.memory import MemoryTransport


def _single_task_plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/done.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


@dataclass
class FakeVirtualCellProvider:
    """Always returns a pre-built WardenLink, for a test that only needs one successful acquire."""

    result: WardenLink
    calls: list[Placement] = field(default_factory=list)

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Record the attempt and return `result`."""
        self.calls.append(placement)
        return self.result


def _fresh_virtual_link(deps: QueenDeps) -> tuple[WardenLink, WardenEnd]:
    """Build a second WardenLink/WardenEnd pair, standing in for a freshly provisioned Cell."""
    cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
    warden_id = new_warden_id(deps.clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    queen_hop = Hop(
        sender=deps.identity.hive_id, recipient=warden_id, node_id=deps.identity.node_id
    )
    warden_hop = Hop(
        sender=warden_id, recipient=deps.identity.hive_id, node_id=deps.identity.node_id
    )
    link = WardenLink(warden_id=warden_id, cell=cell, transport=queen_transport, hop=queen_hop)
    return link, WardenEnd(warden_transport, warden_hop, deps.clock)


async def _kinds(trail: PheromoneTrail) -> list[str]:
    """Return every recorded trail event's own kind, oldest first."""
    return [event.kind for event in await trail.query(TrailQuery())]


async def test_prefer_virtual_dispatches_onto_a_freshly_acquired_virtual_cell() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, real_link, real_warden_end = make_queen_deps(fake_provider=provider)
    fresh_link, fresh_warden_end = _fresh_virtual_link(deps)
    fake_provider = FakeVirtualCellProvider(result=fresh_link)
    backend = VirtualBackendCandidate(
        name="docker",
        capabilities=BackendCapabilities(can_snapshot=False, can_pause=True),
        specs=(
            VirtualCellSpec(
                image="base-ubuntu",
                cpu_cores=1.0,
                memory_bytes=1024**3,
                disk_bytes=8 * 1024**3,
                network_policy=NetworkPolicy.NONE,
                capacity=make_capacity(),
                comb_shield=CombShieldLevel.MEADOW,
                hive_id=deps.identity.hive_id,
            ),
        ),
    )
    deps = dataclasses.replace(
        deps,
        virtual_provider=fake_provider,
        virtual_backends=(backend,),
        placement_policy=PlacementPolicy(prefer="virtual"),
    )
    queen = Queen(deps)
    await queen.attach_warden(real_link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)

    assignment = await fresh_warden_end.wait_for_assignment()
    assert assignment.task_id == goal_id
    assert len(fake_provider.calls) == 1
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.RUNNING
    assert task.warden_id == fresh_link.warden_id
    assert task.cell_id == fresh_link.cell.id
    kinds = await _kinds(deps.trail)
    assert "queen.placed" in kinds
    assert kinds.index("queen.placed") < kinds.index("queen.assigned")
    await real_warden_end.close()
    await fresh_warden_end.close()
