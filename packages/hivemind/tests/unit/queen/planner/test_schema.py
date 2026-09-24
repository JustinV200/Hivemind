"""Unit tests for hivemind.queen.planner.schema.

Covers the structural acceptance rules (roadmap 6.7) and the role rules (roadmap steps 6.9/6.10).

Fits into the Hive:
    Mirrors src/hivemind/queen/planner/schema.py (codingrules section 3); test_plan.py covers the
    rest of the schema through plan_goal's ladder, including PlannedTask.role's carry-through
    into TaskDraft.role.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md,
      "Acceptance can be structural".
    - .claude/roadmap.md steps 6.9 and 6.10 for the Forager and Scout roles this module's role
      rules serve.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cell import TaskNeeds
from hivemind.queen.planner.schema import PlannedPostcondition, PlannedTask
from waggle.messages import PostconditionKind
from waggle.messages.task import SCOUT_REPORT_FILE, WorkerRole

_WELCOMED = PlannedPostcondition(
    kind=PostconditionKind.ELEMENT_TEXT, subject="role=heading;name=Welcome", expected="alice"
)
_ARRIVED = PlannedPostcondition(
    kind=PostconditionKind.URL_MATCHES, subject="page", expected="http://127.0.0.1:8000/home*"
)
_SCOUT_REPORT = PlannedPostcondition(
    kind=PostconditionKind.FILE_EXISTS, subject=SCOUT_REPORT_FILE, argv=(), expected=None
)
_BROWSER = TaskNeeds(exoskeleton=True, browser_only=True)


def _task(
    *acceptance: PlannedPostcondition,
    needs: TaskNeeds | None = None,
    role: WorkerRole = WorkerRole.DRONE,
) -> PlannedTask:
    return PlannedTask(
        key="log-in",
        title="Log in to the fixture site",
        objective="Log in as alice.",
        acceptance=acceptance,
        needs=needs or TaskNeeds(),
        role=role,
    )


def test_page_criteria_are_accepted_on_a_subtask_with_an_exoskeleton() -> None:
    task = _task(_ARRIVED, _WELCOMED, needs=_BROWSER)

    assert [item.kind for item in task.acceptance] == [
        PostconditionKind.URL_MATCHES,
        PostconditionKind.ELEMENT_TEXT,
    ]


def test_page_criteria_are_refused_on_a_subtask_without_one() -> None:
    with pytest.raises(ValidationError, match="ELEMENT_TEXT, URL_MATCHES reads a browser page"):
        _task(_ARRIVED, _WELCOMED)


@pytest.mark.parametrize(
    ("kind", "subject", "expected"),
    [
        (PostconditionKind.REGION_CHANGED, "0,0,10,10", None),  # Needs a before-digest.
        (PostconditionKind.HTTP_STATUS, "http://127.0.0.1:8000/", "200"),  # Not checkable yet.
    ],
)
def test_kinds_no_warden_can_check_stay_refused(
    kind: PostconditionKind, subject: str, expected: str | None
) -> None:
    with pytest.raises(ValidationError, match="cannot be checked by this Hive yet"):
        PlannedPostcondition(kind=kind, subject=subject, expected=expected)


def test_an_element_subject_that_names_nothing_is_refused_inside_the_ladder() -> None:
    # "label=" is the label form with no label: ElementTarget.from_subject cannot build it.
    # (Anything without a known key= prefix is read as a CSS selector, so it always parses.)
    with pytest.raises(ValidationError, match="label"):
        PlannedPostcondition(kind=PostconditionKind.ELEMENT_TEXT, subject="label=", expected="x")


# ──────────────────────────────────────────────────────────────────────────────
# role (roadmap steps 6.9/6.10)
# ──────────────────────────────────────────────────────────────────────────────


def test_role_defaults_to_drone() -> None:
    task = _task(_SCOUT_REPORT)

    assert task.role is WorkerRole.DRONE


@pytest.mark.parametrize(
    "role", [WorkerRole.GUARD_BEE, WorkerRole.UNDERTAKER, WorkerRole.HOUSE_BEE]
)
def test_role_rejects_a_role_the_planner_may_never_assign(role: WorkerRole) -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        _task(_SCOUT_REPORT, role=role)


def test_forager_role_is_accepted_with_an_exoskeleton() -> None:
    task = _task(_ARRIVED, _WELCOMED, needs=_BROWSER, role=WorkerRole.FORAGER)

    assert task.role is WorkerRole.FORAGER


def test_forager_role_is_refused_without_an_exoskeleton() -> None:
    # A Forager with no Exoskeleton has no page or screen to act on (module docstring).
    with pytest.raises(ValidationError, match=r"role=FORAGER requires needs\.exoskeleton"):
        _task(_SCOUT_REPORT, role=WorkerRole.FORAGER)


def test_scout_role_is_accepted_with_exactly_one_file_exists_criterion_on_its_report() -> None:
    task = _task(_SCOUT_REPORT, role=WorkerRole.SCOUT)

    assert task.role is WorkerRole.SCOUT
    assert task.acceptance == (_SCOUT_REPORT,)


def test_scout_role_is_refused_with_more_than_one_criterion() -> None:
    with pytest.raises(ValidationError, match="exactly one acceptance criterion"):
        _task(_SCOUT_REPORT, _ARRIVED, needs=_BROWSER, role=WorkerRole.SCOUT)


def test_scout_role_is_refused_with_the_wrong_kind() -> None:
    wrong_kind = PlannedPostcondition(
        kind=PostconditionKind.COMMAND_EXITS_ZERO, subject="check", argv=("true",)
    )
    with pytest.raises(ValidationError, match="FILE_EXISTS"):
        _task(wrong_kind, role=WorkerRole.SCOUT)


def test_scout_role_is_refused_with_the_wrong_file() -> None:
    wrong_file = PlannedPostcondition(
        kind=PostconditionKind.FILE_EXISTS, subject="scratch/done.txt", argv=(), expected=None
    )
    with pytest.raises(ValidationError, match=SCOUT_REPORT_FILE):
        _task(wrong_file, role=WorkerRole.SCOUT)
