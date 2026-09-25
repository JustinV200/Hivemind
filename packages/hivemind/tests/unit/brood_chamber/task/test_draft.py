"""Unit tests for hivemind.brood_chamber.task.draft: TaskDraft and TaskGraphDraft.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/task/draft.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.task.draft for the module under test.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task_spec
from pydantic import ValidationError

from hivemind.brood_chamber.task.draft import TaskDraft, TaskGraphDraft
from waggle.messages import PlannedLeaving
from waggle.messages.task import WorkerRole

# ──────────────────────────────────────────────────────────────────────────────
# TaskDraft.role (roadmap steps 6.9/6.10)
# ──────────────────────────────────────────────────────────────────────────────


def test_task_draft_role_defaults_to_drone() -> None:
    draft = TaskDraft(
        key="task",
        title="Do the thing",
        objective="Do the thing.",
        acceptance=make_task_spec().acceptance,
    )

    assert draft.role is WorkerRole.DRONE


def test_task_draft_rejects_a_role_the_task_graph_never_assigns() -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        TaskDraft(
            key="task",
            title="Do the thing",
            objective="Do the thing.",
            acceptance=make_task_spec().acceptance,
            role=WorkerRole.GUARD_BEE,
        )


# ──────────────────────────────────────────────────────────────────────────────
# TaskDraft / TaskGraphDraft
# ──────────────────────────────────────────────────────────────────────────────


def _draft(key: str, depends_on: tuple[str, ...] = ()) -> TaskDraft:
    """Build one valid TaskDraft for the graph tests below."""
    return TaskDraft(
        key=key,
        title=f"Task {key}",
        objective=f"Do the work for {key}.",
        acceptance=make_task_spec().acceptance,
        depends_on=depends_on,
    )


def test_task_draft_rejects_a_malformed_key() -> None:
    with pytest.raises(ValidationError):
        _draft("Not Valid!")


def test_task_graph_draft_accepts_a_linear_chain() -> None:
    graph = TaskGraphDraft(tasks=(_draft("a"), _draft("b", ("a",))))

    assert [entry.key for entry in graph.tasks] == ["a", "b"]


def test_task_graph_draft_rejects_a_duplicate_key() -> None:
    with pytest.raises(ValidationError, match="duplicate key"):
        TaskGraphDraft(tasks=(_draft("a"), _draft("a")))


def test_task_graph_draft_rejects_an_unknown_dependency_key() -> None:
    with pytest.raises(ValidationError, match="unknown key"):
        TaskGraphDraft(tasks=(_draft("a", ("nope",)),))


def test_task_graph_draft_rejects_a_self_dependency() -> None:
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        TaskGraphDraft(tasks=(_draft("a", ("a",)),))


def test_task_graph_draft_rejects_a_cycle() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        TaskGraphDraft(tasks=(_draft("a", ("b",)), _draft("b", ("a",))))


def test_task_graph_draft_rejects_an_empty_tasks_tuple() -> None:
    with pytest.raises(ValidationError):
        TaskGraphDraft(tasks=())


def test_task_graph_draft_json_round_trips() -> None:
    original = TaskGraphDraft(tasks=(_draft("a"), _draft("b", ("a",))))

    restored = TaskGraphDraft.model_validate_json(original.model_dump_json())

    assert restored == original


def test_task_draft_leaves_defaults_to_empty_and_round_trips_a_declared_leaving() -> None:
    assert _draft("a").leaves == ()

    leaving = PlannedLeaving(pattern="/opt/project", reason="Set up a project in /opt/project.")
    draft = TaskDraft(
        key="a",
        title="Task a",
        objective="Install the project.",
        acceptance=make_task_spec().acceptance,
        leaves=(leaving,),
    )

    assert draft.leaves == (leaving,)
    assert TaskDraft.model_validate_json(draft.model_dump_json()).leaves == (leaving,)
