"""Tests for hivemind.memory.handoff: Decision, Handoff round-trip and rejection.

Fits into the Hive:
    Mirrors src/hivemind/memory/handoff.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.handoff for the module under test.
"""

from __future__ import annotations

import pytest
from builders.memory import make_handoff
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.memory.handoff import MAX_GOAL_CHARS, MAX_NOTES_CHARS, Decision, Handoff


def test_decision_round_trips_through_json() -> None:
    decision = Decision(what="Chose approach A", why="It was faster to implement")

    restored = Decision.model_validate_json(decision.model_dump_json())

    assert restored == decision


def test_handoff_round_trips_through_json() -> None:
    handoff = make_handoff()

    restored = Handoff.model_validate_json(handoff.model_dump_json())

    assert restored == handoff


def test_handoff_rejects_an_empty_goal() -> None:
    with pytest.raises(ValidationError):
        make_handoff(goal="")


def test_handoff_rejects_a_goal_over_the_cap() -> None:
    with pytest.raises(ValidationError):
        make_handoff(goal="x" * (MAX_GOAL_CHARS + 1))


def test_handoff_rejects_notes_over_the_cap() -> None:
    with pytest.raises(ValidationError):
        make_handoff(notes="x" * (MAX_NOTES_CHARS + 1))


def test_handoff_rejects_a_decision_with_no_reason() -> None:
    with pytest.raises(ValidationError):
        Decision(what="Did a thing", why="")


def test_handoff_carries_its_clearance() -> None:
    handoff = make_handoff(clearance=HoneyClearance.C2)

    assert handoff.clearance is HoneyClearance.C2


def test_handoff_pinned_facts_default_shape_is_a_tuple() -> None:
    handoff = make_handoff(pinned_facts=("Always run tests first.",))

    assert handoff.pinned_facts == ("Always run tests first.",)
