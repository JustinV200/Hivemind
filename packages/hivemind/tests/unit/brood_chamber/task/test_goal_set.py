"""Tests for hivemind.brood_chamber.task.goal_set: a goal's capability set, as every task stores it.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/task/goal_set.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.task.goal_set for the module under test.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task_spec
from pydantic import ValidationError

from hivemind.brood_chamber.task.goal_set import MAX_GOAL_CAPABILITIES, canonical_goal_set


def test_none_means_no_ceiling_and_passes_through() -> None:
    assert canonical_goal_set(None) is None


def test_an_empty_set_stays_empty_a_goal_allowed_nothing() -> None:
    assert canonical_goal_set(()) == ()


def test_a_set_is_stored_sorted_and_without_duplicates() -> None:
    assert canonical_goal_set(("tool:*", "cell:virtual", "tool:*")) == ("cell:virtual", "tool:*")


def test_a_string_that_is_no_capability_is_refused() -> None:
    with pytest.raises(ValueError, match="not a goal capability set"):
        canonical_goal_set(("not a capability",))


def test_a_task_spec_canonicalises_its_goal_set() -> None:
    spec = make_task_spec(capabilities=("tool:*", "cell:virtual"))

    assert spec.capabilities == ("cell:virtual", "tool:*")


def test_a_task_spec_defaults_to_no_ceiling() -> None:
    assert make_task_spec().capabilities is None


def test_a_task_spec_refuses_more_capabilities_than_the_wire_carries() -> None:
    too_many = tuple(f"tool:t{index}" for index in range(MAX_GOAL_CAPABILITIES + 1))

    with pytest.raises(ValidationError):
        make_task_spec(capabilities=too_many)
