"""Tests for hivemind.supervision.attendant.items: InboxKind and InboxItem.

Fits into the Hive:
    Mirrors src/hivemind/supervision/attendant/items.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.attendant.items for the module under test.
"""

from __future__ import annotations

import pytest
from builders.supervision import make_inbox_item
from pydantic import ValidationError

from hivemind.supervision.alarm import AlarmSeverity
from hivemind.supervision.attendant.items import InboxItem, InboxKind


def test_make_inbox_item_builds_a_valid_item() -> None:
    item = make_inbox_item()

    assert item.kind is InboxKind.WAGGLE_MESSAGE
    assert item.severity is None


def test_make_inbox_item_alarm_default_carries_a_severity() -> None:
    item = make_inbox_item(kind=InboxKind.ALARM)

    assert item.severity is AlarmSeverity.WARNING


def test_inbox_item_is_frozen() -> None:
    item = make_inbox_item()

    with pytest.raises(ValidationError, match="frozen"):
        item.principal = "someone-else"  # type: ignore[misc]  # The assignment is the test.


def test_inbox_item_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        InboxItem.model_validate({**make_inbox_item().model_dump(), "extra": "nope"})


def test_inbox_item_accepts_an_arbitrary_payload_object() -> None:
    payload = object()

    item = make_inbox_item(payload=payload)

    assert item.payload is payload


@pytest.mark.parametrize("budget", [0.0, -1.0])
def test_inbox_item_rejects_a_non_positive_latency_budget(budget: float) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        make_inbox_item(latency_budget_s=budget)


def test_inbox_item_accepts_a_positive_latency_budget() -> None:
    item = make_inbox_item(latency_budget_s=5.0)

    assert item.latency_budget_s == 5.0


def test_inbox_item_carries_no_correlation_id_unless_given_one() -> None:
    """Roadmap step 7.8: additive and defaulted, so every earlier item still builds."""
    assert make_inbox_item().correlation_id is None
    assert make_inbox_item(correlation_id="msg_answered").correlation_id == "msg_answered"


def test_a_guard_request_item_is_scored_by_its_kind_and_age_alone() -> None:
    item = make_inbox_item(kind=InboxKind.GUARD_REQUEST, principal="guard")

    assert (item.severity, item.task_id, item.latency_budget_s) == (None, None, None)


@pytest.mark.parametrize(
    "extra",
    [
        {"severity": AlarmSeverity.CRITICAL},
        {"task_id": "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"},
        {"latency_budget_s": 1.0},
    ],
    ids=["severity", "task", "latency"],
)
def test_a_guard_request_item_refuses_anything_that_would_lift_its_score(
    extra: dict[str, object],
) -> None:
    fields = {**make_inbox_item(kind=InboxKind.GUARD_REQUEST).model_dump(), **extra}

    with pytest.raises(ValidationError, match="scored by its kind and its age alone"):
        InboxItem.model_validate(fields)
