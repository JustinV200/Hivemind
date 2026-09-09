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
