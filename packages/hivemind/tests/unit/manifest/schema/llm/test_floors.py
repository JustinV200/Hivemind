"""Tests for hivemind.manifest.schema.llm.floors and LlmSection's ``[llm.floors]`` table.

Fits into the Hive:
    Mirrors src/hivemind.manifest.schema.llm.floors.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.llm.floors for the module under test.
    - hivemind.manifest.schema.llm for LlmSection, which embeds the table.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.forage import ModelSlot
from hivemind.manifest.schema.llm import LlmSection
from hivemind.manifest.schema.llm.floors import (
    DEFAULT_FLOOR,
    DEFAULT_SLOT_FLOORS,
    complete_floors,
    reject_unknown_floor_keys,
)

_SLOTS = {slot.manifest_key: {"provider": "anthropic", "model": "m"} for slot in ModelSlot}


def _section(floors: dict[str, int] | None = None) -> LlmSection:
    return LlmSection.model_validate(
        {"providers": {"anthropic": {"kind": "anthropic"}}, "slots": _SLOTS, "floors": floors or {}}
    )


def test_complete_floors_fills_every_slot_from_the_shipped_defaults() -> None:
    floors = complete_floors({})

    assert set(floors) == set(ModelSlot)
    assert floors[ModelSlot.QUEEN] == 5
    assert floors[ModelSlot.ATTENDANT] == 1
    assert floors[ModelSlot.WORKER] == DEFAULT_FLOOR


def test_complete_floors_lets_the_operators_value_win_over_the_default() -> None:
    floors = complete_floors({"queen": 3, "attendant": 2})

    assert floors[ModelSlot.QUEEN] == 3
    assert floors[ModelSlot.ATTENDANT] == 2
    assert floors[ModelSlot.RIPENER] == DEFAULT_SLOT_FLOORS[ModelSlot.RIPENER]


def test_reject_unknown_floor_keys_names_the_offender_and_the_legal_keys() -> None:
    with pytest.raises(ValueError, match="local_worker") as raised:
        reject_unknown_floor_keys({"worker": 2, "local_worker": 3})

    assert "queen" in str(raised.value)


def test_llm_section_floors_default_to_empty_and_complete_to_the_defaults() -> None:
    section = _section()

    assert section.floors == {}
    assert dict(section.grade_floors()) == dict(complete_floors({}))


def test_llm_section_grade_floors_applies_the_manifests_table() -> None:
    section = _section({"judge": 4})

    assert section.grade_floors()[ModelSlot.JUDGE] == 4
    assert section.grade_floors()[ModelSlot.WORKER] == DEFAULT_FLOOR


def test_llm_section_rejects_a_floor_for_a_key_that_is_not_a_model_slot() -> None:
    with pytest.raises(ValidationError, match="not a model slot"):
        _section({"nonsense": 3})


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_llm_section_rejects_a_floor_outside_one_to_five(bad: int) -> None:
    with pytest.raises(ValidationError):
        _section({"worker": bad})
