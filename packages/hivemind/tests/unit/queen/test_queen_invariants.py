"""Tests for hivemind.queen.queen.Queen: "no session, no registry", and trail event ordering.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_dispatch.py, test_queen_results.py, test_queen_alarms.py,
    test_queen_questions.py, test_queen_liveness.py and test_queen_supervisor.py. Exercises the
    module docstring's own claim -- "The Queen holds no hivemind.cell.CellSession and no Comb
    Registry, anywhere in her own instance attributes or QueenDeps's own fields (docs/adr/0019; a
    test introspects both)" -- and the Pheromone Trail's own recorded order across a full
    submit-dispatch-complete cycle.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md for why the
      Queen holds no session and no registry.
    - hivemind.pheromone.trail.protocol for TRAIL_ORDER_KEY, the ordering `test_trail_events_are_
      recorded_in_the_order_they_happened` relies on.
"""

from __future__ import annotations

import asyncio
import dataclasses
import typing

from builders.queen import make_queen_deps, plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.queen import Queen
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskOutcome, TaskResult

_FORBIDDEN_NAMES = ("CellSession", "CombRegistry")
_FORBIDDEN_MODULE_FRAGMENT = "royal_jelly"  # "Comb Registry" is hivemind.royal_jelly.registry.


def _two_task_plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/root.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            },
            {
                "key": "child",
                "title": "Child task",
                "objective": "Depends on the root task.",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/child.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": ["root"],
            },
        ]
    }


def test_queen_deps_fields_never_type_hint_a_cell_session_or_a_comb_registry() -> None:
    for cls in (QueenDeps, WardenLink):
        hints = typing.get_type_hints(cls)
        for field in dataclasses.fields(cls):
            hint_text = str(hints[field.name])
            for forbidden in _FORBIDDEN_NAMES:
                assert forbidden not in hint_text, f"{cls.__name__}.{field.name}: {hint_text}"
            assert _FORBIDDEN_MODULE_FRAGMENT not in hint_text, f"{cls.__name__}.{field.name}"


async def test_queen_instance_never_holds_a_cell_session_anywhere_in_her_own_state() -> None:
    # CellSession is a plain (non-@runtime_checkable) Protocol, so isinstance() cannot be used
    # against it (mypy itself rejects it); this checks structurally instead, exactly the shape a
    # CellSession implementation exposes (hivemind.cell.session's own module docstring).
    provider = FakeLLMProvider(responder=plan_responder(_two_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("Two tasks.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()

    for name, value in vars(queen).items():
        assert not _looks_like_a_cell_session(value), f"Queen.{name} holds a CellSession-like value"
        for nested in _flatten(value):
            assert not _looks_like_a_cell_session(nested), (
                f"Queen.{name} nests a CellSession-like value"
            )
    await warden_end.close()


_CELL_SESSION_SHAPE = ("scratch_dir", "is_open", "exec", "put_file", "get_file", "delete_file")


def _looks_like_a_cell_session(value: object) -> bool:
    """Return whether `value` structurally satisfies every CellSession member, name for name."""
    return all(hasattr(value, name) for name in _CELL_SESSION_SHAPE)


def _flatten(value: object) -> tuple[object, ...]:
    """Return the direct elements of `value` when it is a dict, tuple or list; else nothing."""
    if isinstance(value, dict):
        return tuple(value.values())
    if isinstance(value, tuple | list):
        return tuple(value)
    return ()


async def test_stop_leaves_no_pending_tasks_behind_a_concurrently_running_run_loop() -> None:
    """This dispatch's own shutdown-hygiene proof: `stop()` reaps every task it owns.

    Before this fix, `Queen.stop()` never reaped `_receive_tasks`: the attached Warden's own
    receive task (`hivemind.queen.queen._next_or_none`) was simply abandoned, pending, once
    `run()` returned -- exactly the `asyncio` "Task was destroyed but it is pending!" warning the
    e2e suite's own live log showed for this class before the fix.
    """
    deps, link, warden_end = make_queen_deps()
    queen = Queen(deps)
    queen.attach_warden(link)
    before = asyncio.all_tasks() - {asyncio.current_task()}
    run_task = asyncio.ensure_future(queen.run())
    await asyncio.sleep(0)  # Let the first tick start its own receive task on the attached link.

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    after = asyncio.all_tasks() - {asyncio.current_task()}
    assert after == before
    await warden_end.close()


async def test_trail_events_are_recorded_in_the_order_they_happened() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_two_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Two tasks.", clearance=HoneyClearance.C1)
    root_assignment = await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    result = TaskResult(
        task_id=root_assignment.task_id,
        attempt=1,
        outcome=TaskOutcome.SUCCEEDED,
        summary="Root done.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=link.warden_id,
        handoff=None,
        spend=0.0,
        reason="Root task finished.",
    )
    await warden_end.send(result)
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    events = await deps.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    # TRAIL_ORDER_KEY keeps same-timestamp events in the order they were recorded (a FakeClock
    # never advances on its own), so this list is exactly the Queen's own recording order: the
    # plan first, the root's own placement second, then (once its TaskResult arrives) the child's.
    assert kinds.index("queen.planned") < kinds.index("queen.assigned")
    assigned = [event for event in events if event.kind == "queen.assigned"]
    assert len(assigned) == 2
    assert assigned[0].subject_id == goal_id
    assert assigned[1].subject_id != goal_id
    await warden_end.close()
