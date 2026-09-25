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

import asyncio
import dataclasses
from datetime import timedelta

from builders.cells import make_capabilities, make_cell
from builders.queen import FORAGE_SOURCE_SEATS, make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import AccessLevel, HoneyClearance
from hivemind.forage import RoleFootprint, RoyalReserve
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import PheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.dispatcher import resume_paused
from hivemind.queen.queen import Queen
from waggle.ids import TaskId, WardenId, new_event_id, new_message_id
from waggle.messages.labels import HandoffRef
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Question
from waggle.messages.task import ExoskeletonNeed, ScoutReport, TaskResult, WorkerRole
from waggle.messages.task import TaskOutcome as WireTaskOutcome


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


def _single_task_plan_with_leaves(goal: str) -> dict[str, object]:
    """`_single_task_plan`, plus one declared leaving, for the roadmap 5.0b carry-through test."""
    plan = _single_task_plan(goal)
    plan["tasks"][0]["leaves"] = [  # type: ignore[index]
        {"pattern": "/opt/project", "reason": "Set up a project in /opt/project."}
    ]
    return plan


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
    # Roadmap step 4.8's own wiring step: the first dispatch to a newly attached Warden also sets
    # its ceilings and writes its Cell's hosting plan, both ahead of the grant they gate nothing.
    assert warden_end.received_kinds == ["ceilings_set", "plan_written", "grant", "assignment"]
    assert assignment.task_id == goal_id
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.RUNNING
    assert task.warden_id == link.warden_id
    assert task.cell_id == link.cell.id
    await warden_end.close()


async def test_submit_goal_carries_a_declared_leaving_all_the_way_to_the_assignment() -> None:
    """Roadmap step 5.0b, end to end: plan -> TaskDraft -> Task row -> the wire task.assign."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan_with_leaves))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Set up a project.", clearance=HoneyClearance.C1)

    assignment = await warden_end.wait_for_assignment()
    assert len(assignment.leaves) == 1
    assert assignment.leaves[0].pattern == "/opt/project"
    assert assignment.leaves[0].reason == "Set up a project in /opt/project."
    task = await deps.chamber.get(goal_id)
    assert task.spec.leaves == assignment.leaves
    await warden_end.close()


def _browser_task_plan(goal: str) -> dict[str, object]:
    """`_single_task_plan`, needing the browser fast path and one network scope (protocol 1.6)."""
    plan = _single_task_plan(goal)
    plan["tasks"][0]["needs"] = {  # type: ignore[index]
        "exoskeleton": True,
        "browser_only": True,
        "network_scopes": ["example.org"],
    }
    return plan


async def test_submit_goal_carries_the_exoskeleton_need_and_scopes_to_the_assignment() -> None:
    """ADR-0031: the need and the scopes reach the Warden that must honour them."""
    provider = FakeLLMProvider(responder=plan_responder(_browser_task_plan))
    capabilities = make_capabilities(has_browser=True, network_scopes=("example.org",))
    cell = make_cell(access_level=AccessLevel.FULL, capabilities=capabilities)
    deps, link, warden_end = make_queen_deps(fake_provider=provider, cell=cell)
    queen = Queen(deps)
    queen.attach_warden(link)

    await queen.submit_goal("Read a page.", clearance=HoneyClearance.C1)

    assignment = await warden_end.wait_for_assignment()
    assert assignment.exoskeleton == ExoskeletonNeed(browser_only=True)
    assert assignment.network_scopes == ("example.org",)
    await warden_end.close()


def _scout_then_drone_plan(goal: str) -> dict[str, object]:
    """A SCOUT root task, plus a DRONE child that depends on it (roadmap steps 6.9/6.10)."""
    return {
        "tasks": [
            {
                "key": "scout",
                "title": "Scout the site",
                "objective": f"Look around before acting on: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scout-report.json",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "role": "SCOUT",
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            },
            {
                "key": "drone",
                "title": "Act on the recon",
                "objective": "Depends on the scout's recon.",
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
                "depends_on": ["scout"],
            },
        ]
    }


async def test_a_succeeded_scout_s_report_reaches_its_dependent_s_assignment() -> None:
    """Roadmap step 6.10, end to end: role and recon carried on the wire TaskAssign."""
    provider = FakeLLMProvider(responder=plan_responder(_scout_then_drone_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("Look around, then act.", clearance=HoneyClearance.C1)
    scout_assignment = await warden_end.wait_for_assignment()
    assert scout_assignment.role is WorkerRole.SCOUT
    assert scout_assignment.recon == ()  # The Scout itself has no Scout dependency of its own.
    run_task = asyncio.ensure_future(queen.run())

    report = ScoutReport(feasible=True, summary="The login form is at /login.")
    await warden_end.send(
        TaskResult(
            task_id=scout_assignment.task_id,
            attempt=1,
            outcome=WireTaskOutcome.SUCCEEDED,
            summary="Looked around.",
            clearance=WireHoneyClearance.C1,
            artifacts=(),
            checked_by=link.warden_id,
            handoff=None,
            spend=0.0,
            reason="Recon complete.",
            scout_report=report,
        )
    )
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    drone_assignment = warden_end.assignments[1]
    assert drone_assignment.task_id != scout_assignment.task_id
    assert drone_assignment.role is WorkerRole.DRONE  # The plan never set one: the schema default.
    assert drone_assignment.recon == (report,)
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


async def test_overriding_reserve_reaches_the_allocator_and_can_zero_it_out() -> None:
    """QueenDeps' own `reserve` field (added this dispatch) reaches the allocator, not just sits.

    It reaches `hivemind.forage.allocate.grant` through `dispatcher._grant_inputs`: `builders.
    queen`'s own forage map declares `FORAGE_SOURCE_SEATS` seats, comfortably clearing the default
    `RoyalReserve`'s own seats=1, so an ordinary test's default grant is positive (see that
    constant's own comment for the fixture trap this sidesteps -- `.claude/phase-4-handoff.md`
    section 4.2 item 1). Overriding `reserve.seats` to claim every seat the map offers instead
    drives the fresh grant's own max_sub_bees to 0, and since this dispatch's own zero-grant fix
    (module docstring of `hivemind.queen.dispatcher`) a grant that empty is never sent at all: it
    is denied on the trail and the task fails at once instead of parking RUNNING until the goal's
    timeout elapses.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    default_grant = await warden_end.wait_for_grant()
    await warden_end.close()
    assert default_grant.max_sub_bees > 0  # builders.queen's own map/reserve defaults clear.

    exhausting_reserve = RoyalReserve(
        seats=FORAGE_SOURCE_SEATS, memory_bytes=0, headroom_fraction=0.0
    )
    provider2 = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps2, link2, warden_end2 = make_queen_deps(fake_provider=provider2, reserve=exhausting_reserve)
    queen2 = Queen(deps2)
    queen2.attach_warden(link2)
    goal_id2 = await queen2.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    task2 = await deps2.chamber.get(goal_id2)
    assert task2.status is TaskStatus.FAILED
    assert task2.outcome is not None
    assert "zero sub-bees" in task2.outcome.summary
    await warden_end2.close()


async def test_overriding_footprints_and_grant_ttl_change_the_computed_grant() -> None:
    """QueenDeps' own `footprints`/`grant_ttl_s` fields (added this dispatch) reach the allocator.

    Through the same call as `reserve` above: a larger DRONE memory footprint drives max_sub_bees
    down (from 4, the fixture's own map with the seat pool unlocked and the default footprint, to
    2) without zeroing it. A footprint no Cell can bear at all is refused one step earlier, by
    placement rule 5 (`test_a_footprint_no_cell_can_bear_fails_placement_instead_of_a_zero_bee_
    grant`, roadmap step 5.7); a reserve that zeroes the grant on a placeable Cell is caught by
    `test_a_zero_grant_is_denied_and_fails_the_task_instead_of_being_sent`, since a zero grant is
    never sent to the wire at all. A named grant_ttl_s lands exactly on the wire grant's own
    expires_at.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    unlocked_reserve = RoyalReserve(seats=0, memory_bytes=0, headroom_fraction=0.0)
    larger_footprint = RoleFootprint(
        cpu_cores=1.0,
        memory_bytes=3 * 1024**3,
        seats=1,
        token_rate_per_minute=1_000.0,
        exoskeleton_extra_memory_bytes=0,
    )
    deps, link, warden_end = make_queen_deps(
        fake_provider=provider,
        reserve=unlocked_reserve,
        footprints={WorkerRole.DRONE: larger_footprint},
        grant_ttl_s=42.0,
    )
    queen = Queen(deps)
    queen.attach_warden(link)

    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    fresh_grant = await warden_end.wait_for_grant()

    assert fresh_grant.max_sub_bees == 2  # by_memory now binds, below the unconstrained cap of 4.
    assert fresh_grant.expires_at == deps.clock.now() + timedelta(seconds=42.0)
    await warden_end.close()


async def test_a_footprint_no_cell_can_bear_fails_placement_instead_of_a_zero_bee_grant() -> None:
    """QueenDeps' own `footprints` field reaches placement, and rule 5 refuses an unbearable one.

    The other half of the zero-grant story, and the reason both tests are honest under the merged
    code: `hivemind.queen.dispatcher.snapshot._has_free_capacity` measures a candidate Cell against
    the DRONE footprint's own memory alone, never against the `RoyalReserve`. A footprint larger
    than the host's free memory is therefore refused by placement rule 5 ("Forage must cover the
    grant", ADR-0028) before any grant is sized -- while a reserve that claims every seat leaves
    the Cell placeable and is caught one step later, by the zero-grant denial
    (`test_overriding_reserve_reaches_the_allocator_and_can_zero_it_out`). Before roadmap step 5.7
    this case produced a max_sub_bees == 0 grant that parked the task until its timeout (the phase
    4 handoff's own open item); now no grant is sent at all and the trail says why.
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
    )
    queen = Queen(deps)
    queen.attach_warden(link)

    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)

    events = await deps.trail.query(TrailQuery())
    decided = [e for e in events if e.kind == "queen.decided"]
    assert decided, "placement failure must be recorded on the trail"
    assert decided[-1].payload.get("reason") == "placement_failed"
    assert "forage.granted" not in await _kinds(deps.trail)
    await warden_end.close()


async def test_a_zero_grant_is_denied_and_fails_the_task_instead_of_being_sent() -> None:
    """The defect fix: `.claude/phase-4-handoff.md` section 4.2 item 1, found for real 2026-09-20.

    A grant computing to `max_sub_bees == 0` used to be sent anyway; the Warden raised
    GRANT_EXCEEDED, the Queen escalated to the human inbox, and the task sat RUNNING for the whole
    timeout with nothing on screen saying why. `hivemind.queen.dispatcher._send_grant_and_assign`
    now denies it and fails the task at once instead. `warden_end.wait_for_plan_written()` is safe
    to await here (unlike a grant or an assignment): `_ensure_warden_provisioned` always sends
    `CeilingsSet` then `PlanWritten` on a Warden's first dispatch, before the zero-grant check
    runs, so both are guaranteed to arrive even though the grant and assignment that would
    normally follow them never do.
    """
    exhausting_reserve = RoyalReserve(
        seats=FORAGE_SOURCE_SEATS, memory_bytes=0, headroom_fraction=0.0
    )
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, reserve=exhausting_reserve)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    # Guaranteed to arrive, both of them, in order (see docstring); pumps the transport without
    # risking a hang on a grant or an assignment that this dispatch never sends.
    await warden_end.wait_for_plan_written()

    # Nothing beyond ceilings/hosting-plan ever reached the Warden: no GrantIssued, no TaskAssign.
    assert warden_end.received_kinds == ["ceilings_set", "plan_written"]
    assert warden_end.grants == []
    assert warden_end.assignments == []

    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.FAILED
    assert task.warden_id is None and task.cell_id is None  # _finish clears placement fields.
    assert task.outcome is not None
    assert "zero sub-bees" in task.outcome.summary

    await _assert_forage_denied_with_figures(deps.trail, goal_id, link.warden_id)
    await warden_end.close()


async def _assert_forage_denied_with_figures(
    trail: PheromoneTrail, task_id: TaskId, warden_id: WardenId
) -> None:
    """Assert one `forage.denied` (and `task.failed`) event exists, with the required figures.

    The allocator's own `reason` plus the free-memory/seat figures that produced zero: the
    required behaviour this dispatch was asked to implement (module docstring of
    `hivemind.queen.dispatcher`'s own zero-grant fix).
    """
    kinds = await _kinds(trail)
    assert "forage.denied" in kinds
    assert "task.failed" in kinds
    denied = next(
        event for event in await trail.query(TrailQuery()) if event.kind == "forage.denied"
    )
    assert denied.payload["task_id"] == task_id
    assert denied.payload["warden_id"] == warden_id
    assert denied.payload["max_sub_bees"] == 0
    assert denied.payload["reserve_seats"] == FORAGE_SOURCE_SEATS
    free_memory_bytes = denied.payload["free_memory_bytes"]
    assert isinstance(free_memory_bytes, int) and free_memory_bytes > 0
    reason = denied.payload["reason"]
    assert isinstance(reason, str) and reason


async def test_a_closed_link_fails_the_task_instead_of_leaving_it_running_forever() -> None:
    """Phase-7 handoff open item 8: `_send_grant_and_assign`'s own new "warden link closed" fold.

    By the time this choke point ever sends, the task is already RUNNING with no legal chamber
    edge back to PENDING (module docstring); this dispatch's own fix mirrors the sibling
    zero-grant case just above and fails the task at once instead of leaving it stuck. A second,
    already-provisioned Warden is needed to reach the guarded sends at all: the very first
    dispatch to any Warden always sends CeilingsSet/PlanWritten first
    (`_ensure_warden_provisioned`), and those two would swallow the closed link the same way,
    proving nothing about the grant/assignment sends this test targets.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("First, ordinary goal.", clearance=HoneyClearance.C1)
    await (
        warden_end.wait_for_assignment()
    )  # Provisions this Warden: ceilings + plan + grant + assign.
    await warden_end.close()  # The link is gone before the second goal is ever dispatched.

    goal_id = await queen.submit_goal(
        "Second goal, onto the now-closed link.", clearance=HoneyClearance.C1
    )

    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.FAILED
    assert task.warden_id is None and task.cell_id is None
    assert task.outcome is not None
    assert "link closed" in task.outcome.summary
    # The second goal's grant was already live in the ledger when its send failed, so it is
    # revoked at once as HOLDER_OFFLINE, not denied and not left for the expiry sweep.
    revoked = [
        event
        for event in await deps.trail.query(TrailQuery(kind="forage.revoked"))
        if event.payload.get("holder") == link.warden_id
    ]
    assert [event.payload["cause"] for event in revoked] == ["HOLDER_OFFLINE"]
    # Only the first goal's own envelopes ever reached this Warden (module docstring): nothing
    # more to pump for the second, closed-link goal.
    assert len(warden_end.assignments) == 1


async def test_resuming_a_paused_task_with_a_zero_grant_fails_it_the_same_way() -> None:
    """`resume_paused` (a `resume_from` resume) hits the same choke point and fails the same way.

    Requirement: "A resumed task (resume_from) with a zero grant fails the same way." Starts from
    a normal dispatch (a positive default grant, module docstring), pauses it, then resumes it
    through `dispatcher.resume_paused` directly -- the one dispatcher-path entry point Clustering's
    own resume uses -- over a `QueenDeps` swapped to an exhausting reserve, with a real
    `HandoffRef` so the resume_from-carrying path is exercised, not just the fresh-attempt one.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()  # The normal, positive-grant dispatch.

    await deps.chamber.pause(goal_id, "test: pausing before a zero-grant resume")
    resume_from = HandoffRef(
        event_id=new_event_id(deps.clock),
        written_at=deps.clock.now(),
        clearance=WireHoneyClearance.C1,
    )
    exhausting_reserve = RoyalReserve(
        seats=FORAGE_SOURCE_SEATS, memory_bytes=0, headroom_fraction=0.0
    )
    # QueenDeps is frozen (codingrules 8.5): swap in a reserve that exhausts the fixture's own
    # forage map, sharing every other collaborator (ledger, chamber, trail) with the real deps.
    zero_deps = dataclasses.replace(deps, reserve=exhausting_reserve)

    await resume_paused(zero_deps, [link], goal_id, resume_from, "test resume")

    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    assert "zero sub-bees" in task.outcome.summary
    # _send_grant_and_assign's own zero-grant branch returns before either wire send (dispatcher
    # module docstring), so nothing beyond the original dispatch's own envelopes ever exists here
    # to pump; polling `warden_end` further would hang rather than prove anything (the transport's
    # own module docstring: "no timeout is needed because only the peer ... or cancellation can
    # end it").
    await _assert_forage_denied_with_figures(deps.trail, goal_id, link.warden_id)
    await warden_end.close()


async def test_a_question_right_after_dispatch_never_raises_invalid_transition() -> None:
    """Fix 4a: chamber.assign/start land before dispatch_one's own wire sends (module docstring).

    `hivemind.queen.dispatcher._dispatch_one`'s own required order is what closes the race
    `tests.e2e.test_kernel_on_hive_stand`'s own module docstring names: a fast sub-bee whose very
    first action is `ask`, forwarded straight back, must never reach `Queen._act`'s own
    BLOCK_ON_QUESTION handling while the chamber still reads ASSIGNED (raising
    `InvalidTransitionError`, which -- before fix 4's own `_recoverable_errors` addition -- would
    also have ended the Queen's own `run()` loop for good). This drives the real tick loop
    concurrently with the dispatch it reacts to, the same shape `test_queen_alarms.py`'s own
    REBIND test already uses for a Warden-forwarded AlarmRaised.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    assignment = await warden_end.wait_for_assignment()
    assert assignment.task_id == goal_id
    question = Question(
        question_id=new_message_id(deps.clock),
        task_id=goal_id,
        asked_by=link.warden_id,
        text="Which season?",
        options=(),
        clearance=WireHoneyClearance.C1,
        asked_at=deps.clock.now(),
    )
    await warden_end.send(question)

    for _ in range(200):
        task = await deps.chamber.get(goal_id)
        if task.status is TaskStatus.BLOCKED:
            break
        assert not run_task.done(), f"Queen.run() ended early: {run_task.exception()}"
        await asyncio.sleep(0)
    else:  # pragma: no cover - defensive
        raise AssertionError("The Question never blocked its task in time.")

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    await warden_end.close()
