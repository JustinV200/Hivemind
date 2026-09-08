"""Tests for hivemind.brood_chamber.task: TaskSpec, TaskOutcome, Task, TaskDraft, TaskGraphDraft.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/task.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.task for the module under test.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_outcome, make_task, make_task_spec
from pydantic import ValidationError

from hivemind.brood_chamber.task import Task, TaskDraft, TaskGraphDraft, TaskOutcome, TaskSpec
from hivemind.brood_chamber.task_state import TaskStatus
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_task_id, new_warden_id

# ──────────────────────────────────────────────────────────────────────────────
# TaskSpec: depends_on
# ──────────────────────────────────────────────────────────────────────────────


def test_task_spec_accepts_valid_unique_dependencies() -> None:
    clock = FakeClock()
    deps = (new_task_id(clock), new_task_id(clock))

    spec = make_task_spec(depends_on=deps)

    assert spec.depends_on == deps


def test_task_spec_rejects_a_duplicate_dependency() -> None:
    clock = FakeClock()
    dep = new_task_id(clock)

    with pytest.raises(ValidationError, match="duplicate"):
        make_task_spec(depends_on=(dep, dep))


def test_task_spec_rejects_a_malformed_dependency_id() -> None:
    with pytest.raises(ValidationError):
        make_task_spec(depends_on=("not-a-task-id",))


def test_task_spec_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        TaskSpec.model_validate(
            {
                "title": "t",
                "objective": "o",
                "acceptance": [],
                "extra": "nope",
            }
        )


def test_task_spec_is_frozen() -> None:
    spec = make_task_spec()

    with pytest.raises(ValidationError, match="frozen"):
        spec.title = "changed"  # type: ignore[misc]  # The assignment is the test.


def test_task_spec_json_round_trips() -> None:
    original = make_task_spec()

    restored = TaskSpec.model_validate_json(original.model_dump_json())

    assert restored == original


# ──────────────────────────────────────────────────────────────────────────────
# TaskOutcome
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.BLOCKED])
def test_task_outcome_rejects_a_non_terminal_status(status: TaskStatus) -> None:
    with pytest.raises(ValidationError, match="terminal"):
        make_outcome(status=status)


def test_task_outcome_requires_verified_by_when_succeeded() -> None:
    with pytest.raises(ValidationError, match="verified_by"):
        make_outcome(status=TaskStatus.SUCCEEDED, verified_by=None)


@pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_task_outcome_rejects_verified_by_when_not_succeeded(status: TaskStatus) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="verified_by"):
        make_outcome(status=status, verified_by=new_warden_id(clock))


def test_task_outcome_accepts_failed_with_no_verified_by() -> None:
    outcome = make_outcome(status=TaskStatus.FAILED)

    assert outcome.verified_by is None


def test_task_outcome_json_round_trips() -> None:
    original = make_outcome()

    restored = TaskOutcome.model_validate_json(original.model_dump_json())

    assert restored == original


# ──────────────────────────────────────────────────────────────────────────────
# Task: outcome <-> terminal status
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status", [TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.PAUSED]
)
def test_task_rejects_a_non_terminal_status_that_carries_an_outcome(status: TaskStatus) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="outcome"):
        make_task(status=status, clock=clock, outcome=make_outcome(clock=clock))


@pytest.mark.parametrize("status", [TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_task_rejects_a_terminal_status_with_no_outcome(status: TaskStatus) -> None:
    with pytest.raises(ValidationError, match="outcome"):
        make_task(status=status, outcome=None)


def test_task_rejects_an_outcome_status_that_does_not_match_task_status() -> None:
    clock = FakeClock()
    mismatched = make_outcome(status=TaskStatus.FAILED, clock=clock)

    with pytest.raises(ValidationError, match="does not match"):
        make_task(status=TaskStatus.SUCCEEDED, clock=clock, outcome=mismatched)


def test_task_accepts_a_matching_terminal_outcome() -> None:
    task = make_task(status=TaskStatus.SUCCEEDED)

    assert task.outcome is not None
    assert task.outcome.status is TaskStatus.SUCCEEDED


# ──────────────────────────────────────────────────────────────────────────────
# Task: pending_question_id <-> BLOCKED
# ──────────────────────────────────────────────────────────────────────────────


def test_task_accepts_blocked_with_a_pending_question_id() -> None:
    task = make_task(status=TaskStatus.BLOCKED)

    assert task.pending_question_id is not None


def test_task_rejects_blocked_with_no_pending_question_id() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="pending_question_id"):
        make_task(
            status=TaskStatus.BLOCKED,
            clock=clock,
            pending_question_id=None,
        )


def test_task_rejects_a_non_blocked_status_that_carries_a_pending_question_id() -> None:
    clock = FakeClock()
    blocked = make_task(status=TaskStatus.BLOCKED, clock=clock)

    with pytest.raises(ValidationError, match="pending_question_id"):
        make_task(
            status=TaskStatus.RUNNING,
            clock=clock,
            warden_id=blocked.warden_id,
            cell_id=blocked.cell_id,
            pending_question_id=blocked.pending_question_id,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Task: warden_id/cell_id <-> placed statuses
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PAUSED],
)
def test_task_requires_placement_for_a_placed_status(status: TaskStatus) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="warden_id and cell_id"):
        make_task(status=status, clock=clock, warden_id=None, cell_id=None)


@pytest.mark.parametrize(
    "status", [TaskStatus.PENDING, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED]
)
def test_task_rejects_a_placement_for_a_non_placed_status(status: TaskStatus) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="must not carry a placement"):
        make_task(
            status=status,
            clock=clock,
            warden_id=new_warden_id(clock),
            cell_id=new_cell_id(clock),
        )


def test_task_rejects_a_warden_id_with_no_cell_id() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="both set or both None"):
        make_task(
            status=TaskStatus.RUNNING,
            clock=clock,
            warden_id=new_warden_id(clock),
            cell_id=None,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Task: no self-dependency, updated_at >= created_at
# ──────────────────────────────────────────────────────────────────────────────


def test_task_rejects_depending_on_itself() -> None:
    clock = FakeClock()
    task_id = new_task_id(clock)

    with pytest.raises(ValidationError, match="depend on itself"):
        make_task(clock=clock, id=task_id, spec=make_task_spec(depends_on=(task_id,)))


def test_task_rejects_updated_at_before_created_at() -> None:
    clock = FakeClock()
    early = clock.now()
    clock.advance(1)
    later = clock.now()

    with pytest.raises(ValidationError, match="precedes"):
        make_task(clock=clock, created_at=later, updated_at=early)


def test_task_accepts_updated_at_equal_to_created_at() -> None:
    clock = FakeClock()
    at = clock.now()

    task = make_task(clock=clock, created_at=at, updated_at=at)

    assert task.updated_at == task.created_at


# ──────────────────────────────────────────────────────────────────────────────
# Task: attempt, fraction_done, frozen, unknown field, JSON round trip
# ──────────────────────────────────────────────────────────────────────────────


def test_task_rejects_an_attempt_below_the_minimum() -> None:
    with pytest.raises(ValidationError):
        make_task(attempt=0)


def test_task_rejects_a_fraction_done_out_of_range() -> None:
    with pytest.raises(ValidationError):
        make_task(fraction_done=1.5)


def test_task_is_frozen() -> None:
    task = make_task()

    with pytest.raises(ValidationError, match="frozen"):
        task.status = TaskStatus.RUNNING  # type: ignore[misc]  # The assignment is the test.


def test_task_rejects_an_unknown_field() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="extra"):
        Task.model_validate(
            {
                "id": new_task_id(clock),
                "goal_id": new_task_id(clock),
                "spec": make_task_spec().model_dump(mode="json"),
                "status": "PENDING",
                "attempt": 1,
                "warden_id": None,
                "cell_id": None,
                "created_at": clock.now().isoformat(),
                "updated_at": clock.now().isoformat(),
                "last_summary": None,
                "fraction_done": None,
                "outcome": None,
                "pending_question_id": None,
                "extra": "nope",
            }
        )


def test_task_json_round_trips_for_every_status() -> None:
    for status in TaskStatus:
        original = make_task(status=status)

        restored = Task.model_validate_json(original.model_dump_json())

        assert restored == original


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
