"""Build valid hivemind.brood_chamber test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about, so a test that only cares about one field writes
`make_task(status=TaskStatus.BLOCKED)` rather than filling in a dozen unrelated fields by hand.
`make_task` in particular fills in `warden_id`, `cell_id`, `outcome` and `pending_question_id`
however the requested `status` requires, so the result always satisfies `Task`'s own cross-field
validators (`hivemind.brood_chamber.task`) without the caller needing to know which combination
that status demands.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/brood_chamber, and by the store and chamber test suites a later
    phase 2 dispatch adds.

Key invariants:
    - Every builder takes an optional `clock: Clock` (default a fresh FakeClock) so every id it
      mints and every timestamp it sets are deterministic across a test run.
    - make_task's result always passes Task's own validators for the requested `status`, for
      every status in TaskStatus, including BLOCKED and PAUSED.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.brood_chamber.task for TaskSpec, TaskOutcome, Task, TaskDraft, TaskGraphDraft.
    - hivemind.brood_chamber.questions for Question and Answer.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.brood_chamber.questions import Answer, AnswerSource, Question
from hivemind.brood_chamber.task import Task, TaskDraft, TaskGraphDraft, TaskOutcome, TaskSpec
from hivemind.brood_chamber.task_state import TERMINAL_STATUSES, TaskStatus
from hivemind.cell import HoneyClearance
from waggle.clock import Clock, FakeClock
from waggle.ids import new_cell_id, new_message_id, new_task_id, new_warden_id, new_worker_id
from waggle.messages import Postcondition, PostconditionKind

# The statuses a Task holds a placement (warden_id/cell_id) in; mirrors
# hivemind.brood_chamber.task's own private _PLACED_STATUSES so make_task can fill the same
# fields that module's validator requires, without importing a private name across the boundary.
_PLACED_STATUSES = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PAUSED}
)

__all__ = [
    "make_answer",
    "make_graph_draft",
    "make_outcome",
    "make_question",
    "make_task",
    "make_task_spec",
]


def _default_acceptance() -> tuple[Postcondition, ...]:
    """Return one trivial, always-valid acceptance criterion for a built TaskSpec/TaskDraft."""
    return (
        Postcondition(
            kind=PostconditionKind.FILE_EXISTS,
            subject="scratch/done.txt",
            argv=(),
            expected=None,
        ),
    )


def make_task_spec(clock: Clock | None = None, **overrides: object) -> TaskSpec:
    """Build a valid TaskSpec, filling in every required field with a plain default.

    Args:
        clock: Reserved for overrides that need to mint an id (e.g. `depends_on`); a fresh
            FakeClock when omitted. Unused by the defaults below, which carry no ids of their own.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TaskSpec.
    """
    fields: dict[str, object] = {
        "title": "Do the thing",
        "objective": "Do the thing, fully, and leave evidence that it was done.",
        "acceptance": _default_acceptance(),
    }
    fields.update(overrides)
    return TaskSpec(**fields)


def make_outcome(
    status: TaskStatus = TaskStatus.SUCCEEDED, clock: Clock | None = None, **overrides: object
) -> TaskOutcome:
    """Build a valid TaskOutcome for a terminal `status`.

    Args:
        status: The terminal status this outcome records; SUCCEEDED by default.
        clock: Source of the verified_by id; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TaskOutcome, with `verified_by` filled in when `status` is SUCCEEDED.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {"status": status, "summary": f"Finished as {status.name}."}
    # verified_by is required exactly when SUCCEEDED (hivemind.brood_chamber.task.TaskOutcome).
    if status is TaskStatus.SUCCEEDED:
        fields["verified_by"] = new_warden_id(active_clock)
    fields.update(overrides)
    return TaskOutcome(**fields)


def make_task(
    status: TaskStatus = TaskStatus.PENDING, clock: Clock | None = None, **overrides: object
) -> Task:
    """Build a valid Task in `status`, filling in whatever fields that status requires.

    Placement (`warden_id`, `cell_id`), `outcome` and `pending_question_id` are each filled in or
    left unset as Task's own validators require for `status`, so `make_task(status=TaskStatus.
    BLOCKED)` is valid on its own with no further overrides.

    Args:
        status: The task's status; PENDING by default.
        clock: Source of every minted id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `status` itself.

    Returns:
        A validated Task.
    """
    active_clock = clock if clock is not None else FakeClock()
    task_id = new_task_id(active_clock)
    fields: dict[str, object] = {
        "id": task_id,
        "goal_id": task_id,  # A standalone build is its own goal; override for a graph member.
        "spec": make_task_spec(clock=active_clock),
        "status": status,
        "created_at": active_clock.now(),
        "updated_at": active_clock.now(),
    }
    _fill_placement(fields, status, active_clock)
    _fill_outcome(fields, status, active_clock)
    _fill_pending_question(fields, status, active_clock)
    fields.update(overrides)
    return Task(**fields)


def make_question(clock: Clock | None = None, **overrides: object) -> Question:
    """Build a valid, unanswered Question (status ASKED, answer None).

    Args:
        clock: Source of every minted id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated Question.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_message_id(active_clock),
        "task_id": new_task_id(active_clock),
        "asked_by": new_worker_id(active_clock),
        "text": "Which of these should I use?",
        "clearance": HoneyClearance.C1,
        "asked_at": active_clock.now(),
    }
    fields.update(overrides)
    return Question(**fields)


def make_answer(clock: Clock | None = None, **overrides: object) -> Answer:
    """Build a valid Answer from the Queen.

    Args:
        clock: Source of the timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated Answer.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "text": "Use the first option.",
        "chosen_option": None,
        "source": AnswerSource.QUEEN,
        "clearance": HoneyClearance.C1,
        "answered_at": active_clock.now(),
    }
    fields.update(overrides)
    return Answer(**fields)


def make_graph_draft(
    keys_and_deps: Mapping[str, tuple[str, ...]], clock: Clock | None = None, **overrides: object
) -> TaskGraphDraft:
    """Build a valid TaskGraphDraft from a mapping of draft key to the keys it depends on.

    Args:
        keys_and_deps: Ordered draft key -> the keys (of other entries in the same mapping) it
            depends on. The first key becomes the goal (`TaskGraphDraft.tasks[0]`).
        clock: Reserved for signature parity with the other builders; unused, since no TaskDraft
            field carries an id or a timestamp.
        **overrides: Field values that replace TaskGraphDraft's own defaults.

    Returns:
        A validated TaskGraphDraft.
    """
    drafts = tuple(
        TaskDraft(
            key=key,
            title=f"Task {key}",
            objective=f"Do the work for {key}.",
            acceptance=_default_acceptance(),
            depends_on=deps,
        )
        for key, deps in keys_and_deps.items()
    )
    fields: dict[str, object] = {"tasks": drafts}
    fields.update(overrides)
    return TaskGraphDraft(**fields)


def _fill_placement(fields: dict[str, object], status: TaskStatus, clock: Clock) -> None:
    """Set warden_id/cell_id in `fields` when `status` is one of the placed statuses."""
    if status in _PLACED_STATUSES:
        fields["warden_id"] = new_warden_id(clock)
        fields["cell_id"] = new_cell_id(clock)


def _fill_outcome(fields: dict[str, object], status: TaskStatus, clock: Clock) -> None:
    """Set outcome in `fields` when `status` is terminal."""
    if status in TERMINAL_STATUSES:
        fields["outcome"] = make_outcome(status=status, clock=clock)


def _fill_pending_question(fields: dict[str, object], status: TaskStatus, clock: Clock) -> None:
    """Set pending_question_id in `fields` when `status` is BLOCKED."""
    if status is TaskStatus.BLOCKED:
        fields["pending_question_id"] = new_message_id(clock)
