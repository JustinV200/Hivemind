"""Tests for hivemind.memory.bee_bread.entry: BeeBreadEntry and BeeBreadEntryKind.

Fits into the Hive:
    Mirrors src/hivemind/memory/bee_bread/entry.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.bee_bread.entry for the module under test.
"""

from __future__ import annotations

import pytest
from builders.memory import make_bee_bread_entry
from pydantic import ValidationError

from hivemind.memory.bee_bread.entry import MAX_ENTRY_TEXT_CHARS, BeeBreadEntry, BeeBreadEntryKind


def test_bee_bread_entry_round_trips_through_json() -> None:
    entry = make_bee_bread_entry()

    restored = BeeBreadEntry.model_validate_json(entry.model_dump_json())

    assert restored == entry


def test_bee_bread_entry_is_frozen_and_forbids_extras() -> None:
    entry = make_bee_bread_entry()

    with pytest.raises(ValidationError, match="frozen"):
        entry.text = "changed"  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        BeeBreadEntry.model_validate({**entry.model_dump(), "nope": 1})


def test_bee_bread_entry_rejects_text_over_the_cap() -> None:
    with pytest.raises(ValidationError):
        make_bee_bread_entry(text="x" * (MAX_ENTRY_TEXT_CHARS + 1))


def test_bee_bread_entry_kind_has_one_member_per_roadmap_step_4_2_shape() -> None:
    assert {member.value for member in BeeBreadEntryKind} == {
        "TASK_HISTORY",
        "TRAIL_EVENT",
        "HANDOFF",
        "NOTE",
        "TRANSCRIPT",
        "TOOL_RESULT",
    }


def test_bee_bread_entry_accepts_a_payload_only_entry_with_no_text() -> None:
    entry = make_bee_bread_entry(
        kind=BeeBreadEntryKind.TRANSCRIPT, ref_ids=(), text=None, payload="the transcript"
    )

    assert entry.text is None
    assert entry.payload == "the transcript"
