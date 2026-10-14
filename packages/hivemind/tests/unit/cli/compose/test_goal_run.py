"""Tests for hivemind.cli.compose.hive.goals: `GoalRun` and `run_goal_with`, the goal's ceiling.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/hive/goals.py (codingrules section 3), for the part roadmap
    step 11.12 added: a goal may be submitted with a capability ceiling, which `hive swarm run`
    uses to pin it to one device's Cell. The submission is the whole behaviour under test, so the
    Hive is a stub holding a clock and a Queen that keeps what `submit_goal` was asked, and the
    follow (which `test_compose` covers end to end against a real Hive) is replaced by a canned
    report. `run_goal` must keep submitting with no ceiling at all: `None` is the operator's own
    local path, which an empty tuple would silently turn into a goal that can be placed nowhere.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.hive.goals for the module under test.
    - tests/unit/cli/test_compose.py for `run_goal` against a real Hive.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from hivemind.cell import HoneyClearance
from hivemind.cli.compose.hive import GoalReport, GoalRun, Hive, run_goal, run_goal_with
from hivemind.cli.compose.hive import goals as goals_module
from waggle.clock import FakeClock
from waggle.ids import TaskId, new_task_id


class _Queen:
    """A Queen that keeps what a goal was submitted with and answers with one goal id."""

    def __init__(self, goal_id: TaskId) -> None:
        """Answer every submission with `goal_id`."""
        self.goal_id = goal_id
        self.submitted: list[tuple[str, HoneyClearance, tuple[str, ...] | None]] = []
        self.survived: list[bool] = []  # Whether each submission promised to survive a lost link.

    async def submit_goal(
        self,
        goal: str,
        *,
        clearance: HoneyClearance,
        capabilities: tuple[str, ...] | None = None,
        survives_link_loss: bool = False,
    ) -> TaskId:
        """Keep the submission's terms and return the goal's id."""
        self.submitted.append((goal, clearance, capabilities))
        self.survived.append(survives_link_loss)
        return self.goal_id


def _hive_and_queen(monkeypatch: pytest.MonkeyPatch) -> tuple[Hive, _Queen, GoalReport]:
    """A stub Hive over a recording Queen, with the follow replaced by one canned report."""
    clock = FakeClock()
    queen = _Queen(new_task_id(clock))
    report = GoalReport(
        goal_id=queen.goal_id,
        tasks=(),
        succeeded=True,
        spend_usd=0.0,
        elapsed_s=0.0,
        timed_out=False,
    )

    async def follow(*_arguments: object) -> GoalReport:
        """Stand in for the poll that ends when every task of the goal is terminal."""
        return report

    monkeypatch.setattr(goals_module, "_follow", follow)
    return cast("Hive", SimpleNamespace(clock=clock, queen=queen)), queen, report


async def test_run_goal_with_submits_the_goal_under_the_ceiling_it_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hive, queen, report = _hive_and_queen(monkeypatch)
    ceiling = ("cell:real:cell_x", "cell:comb_shield:*", "exec:*")

    outcome = await run_goal_with(
        hive, "write a report", GoalRun(HoneyClearance.C2, 30.0, capabilities=ceiling)
    )

    assert outcome is report
    assert queen.submitted == [("write a report", HoneyClearance.C2, ceiling)]


async def test_run_goal_still_submits_with_no_ceiling_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hive, queen, _ = _hive_and_queen(monkeypatch)

    await run_goal(hive, "write a report", clearance=HoneyClearance.C1, timeout_s=30.0)

    # None, not an empty tuple: an empty ceiling would allow no Cell, and None allows every one.
    assert queen.submitted == [("write a report", HoneyClearance.C1, None)]


def test_a_goal_run_has_no_ceiling_and_no_hook_until_it_is_given_them() -> None:
    run = GoalRun(HoneyClearance.C1, 5.0)

    assert (run.capabilities, run.on_event) == (None, None)


async def test_a_goal_run_carries_the_promise_to_survive_a_lost_link_to_the_queen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hive, queen, _ = _hive_and_queen(monkeypatch)

    await run_goal_with(
        hive, "write a report", GoalRun(HoneyClearance.C1, 30.0, survives_link_loss=True)
    )
    await run_goal_with(hive, "write another", GoalRun(HoneyClearance.C1, 30.0))

    # Off unless the operator asked: the default never promises what nobody said.
    assert queen.survived == [True, False]
