"""Unit tests for hivemind.queen.planner.schema: the structural acceptance rules (roadmap 6.7).

Fits into the Hive:
    Mirrors src/hivemind/queen/planner/schema.py (codingrules section 3); test_plan.py covers the
    rest of the schema through plan_goal's ladder.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md,
      "Acceptance can be structural".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cell import TaskNeeds
from hivemind.queen.planner.schema import PlannedPostcondition, PlannedTask
from waggle.messages import PostconditionKind

_WELCOMED = PlannedPostcondition(
    kind=PostconditionKind.ELEMENT_TEXT, subject="role=heading;name=Welcome", expected="alice"
)
_ARRIVED = PlannedPostcondition(
    kind=PostconditionKind.URL_MATCHES, subject="page", expected="http://127.0.0.1:8000/home*"
)
_BROWSER = TaskNeeds(exoskeleton=True, browser_only=True)


def _task(*acceptance: PlannedPostcondition, needs: TaskNeeds | None = None) -> PlannedTask:
    return PlannedTask(
        key="log-in",
        title="Log in to the fixture site",
        objective="Log in as alice.",
        acceptance=acceptance,
        needs=needs or TaskNeeds(),
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
