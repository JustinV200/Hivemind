"""Tests for hivemind.memory.compact.schema: CompactionSchema and its bounds.

Fits into the Hive:
    Mirrors src/hivemind/memory/compact/schema.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.compact.schema for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.memory.compact.schema import MAX_SUMMARY_CHARS, CompactionSchema


def test_compaction_schema_accepts_a_minimal_summary() -> None:
    schema = CompactionSchema(summary="Everything went fine.")

    assert schema.key_facts == ()
    assert schema.open_threads == ()


def test_compaction_schema_rejects_an_empty_summary() -> None:
    with pytest.raises(ValidationError):
        CompactionSchema(summary="")


def test_compaction_schema_rejects_a_summary_over_the_char_cap() -> None:
    with pytest.raises(ValidationError):
        CompactionSchema(summary="x" * (MAX_SUMMARY_CHARS + 1))


def test_compaction_schema_is_frozen_and_forbids_extras() -> None:
    schema = CompactionSchema(summary="Fine.")

    with pytest.raises(ValidationError, match="frozen"):
        schema.summary = "changed"  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        CompactionSchema.model_validate({**schema.model_dump(), "nope": 1})
