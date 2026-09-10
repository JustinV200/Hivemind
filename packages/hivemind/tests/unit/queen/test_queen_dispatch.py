"""Tests for hivemind.queen.queen.Queen: submit_goal and dispatching a ready task.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_results.py, test_queen_alarms.py, test_queen_questions.py, test_queen_liveness.py,
    test_queen_supervisor.py and test_queen_invariants.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.queen, .dispatcher, .planner.plan for the modules under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.forage import RoleFootprint, RoyalReserve
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import PheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.queen import Queen
from waggle.messages.task import WorkerRole


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


async def _kinds(trail: PheromoneTrail) -> list[str]:
    """Return every recorded trail event's own kind, oldest first."""
    return [event.kind for event in await trail.query(TrailQuery())]


async def test_submit_goal_persists_the_plan_and_records_queen_planned() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)

    task = await deps.chamber.get(goal_id)
    assert task.spec.title == "Root task"
    assert "queen.planned" in await _kinds(deps.trail)
    await warden_end.close()


async def test_submit_goal_dispatches_the_ready_task_grant_then_assignment_in_order() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)

    assignment = await warden_end.wait_for_assignment()
    assert warden_end.received_kinds == ["grant", "assignment"]
    assert assignment.task_id == goal_id
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.RUNNING
    assert task.warden_id == link.warden_id
    assert task.cell_id == link.cell.id
    await warden_end.close()


async def test_submit_goal_records_queen_assigned_for_the_dispatched_task() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()

    events = await deps.trail.query(TrailQuery())
    assigned = [event for event in events if event.kind == "queen.assigned"]
    assert len(assigned) == 1
    assert assigned[0].subject_id == goal_id
    await warden_end.close()


async def test_forage_granted_is_recorded_after_queen_assigned_with_the_task_and_warden() -> None:
    """The first test to assert `forage.granted` exists at all, in the required order.

    `hivemind.queen.dispatcher` (roadmap step 3.21, second half) records the one `forage.granted`
    ForageEvent no other module writes yet (module docstring: "placed" precedes "granted").
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()

    kinds = await _kinds(deps.trail)
    assert kinds.index("queen.assigned") < kinds.index("forage.granted")
    events = await deps.trail.query(TrailQuery())
    granted = next(event for event in events if event.kind == "forage.granted")
    assert granted.payload["task_id"] == goal_id
    assert granted.payload["warden_id"] == link.warden_id
    await warden_end.close()


async def test_overriding_reserve_unlocks_the_seat_the_default_reserve_exhausts() -> None:
    """QueenDeps' own `reserve` field (added this dispatch) reaches the allocator, not just sits.

    It reaches `hivemind.forage.allocate.grant` through `dispatcher._grant_inputs`: `builders.
    queen.make_queen_deps`' own forage map has one source with one spec seat, which the default
    `RoyalReserve`'s own seats=1 fully claims, so the default grant's own max_sub_bees is 0;
    overriding reserve to seats=0 frees that one seat and a positive max_sub_bees results.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    default_grant = await warden_end.wait_for_grant()
    await warden_end.close()
    assert default_grant.max_sub_bees == 0

    unlocked_reserve = RoyalReserve(seats=0, memory_bytes=0, headroom_fraction=0.0)
    provider2 = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps2, link2, warden_end2 = make_queen_deps(fake_provider=provider2, reserve=unlocked_reserve)
    queen2 = Queen(deps2)
    queen2.attach_warden(link2)
    await queen2.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    overridden_grant = await warden_end2.wait_for_grant()
    await warden_end2.close()
    assert overridden_grant.max_sub_bees > 0


async def test_overriding_footprints_and_grant_ttl_change_the_computed_grant() -> None:
    """QueenDeps' own `footprints`/`grant_ttl_s` fields (added this dispatch) reach the allocator.

    Through the same call as `reserve` above: a much larger DRONE memory footprint drives
    max_sub_bees back down to 0 even with the seat pool unlocked, and a named grant_ttl_s lands
    exactly on the wire grant's own expires_at.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    unlocked_reserve = RoyalReserve(seats=0, memory_bytes=0, headroom_fraction=0.0)
    huge_footprint = RoleFootprint(
        cpu_cores=1.0,
        memory_bytes=100 * 1024**3,
        seats=1,
        token_rate_per_minute=1_000.0,
        exoskeleton_extra_memory_bytes=0,
    )
    deps, link, warden_end = make_queen_deps(
        fake_provider=provider,
        reserve=unlocked_reserve,
        footprints={WorkerRole.DRONE: huge_footprint},
        grant_ttl_s=42.0,
    )
    queen = Queen(deps)
    queen.attach_warden(link)

    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    fresh_grant = await warden_end.wait_for_grant()

    assert fresh_grant.max_sub_bees == 0  # by_memory is now the binding constraint, at zero.
    assert fresh_grant.expires_at == deps.clock.now() + timedelta(seconds=42.0)
    await warden_end.close()
