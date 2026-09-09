"""Tests for hivemind.forage.slots: ModelSlot and Effort.

Fits into the Hive:
    Mirrors src/hivemind/forage/slots.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.slots for the module under test.
"""

from __future__ import annotations

import re

import pytest

from hivemind.forage.slots import Effort, ModelSlot
from waggle.messages.base import SLOT_PATTERN
from waggle.messages.forage import Effort as WireEffort

EXPECTED_SLOTS = (
    "QUEEN",
    "ATTENDANT",
    "WARDEN",
    "WORKER",
    "RIPENER",
    "SCAFFOLDER",
    "EMBEDDER",
    "JUDGE",
    "TRANSCRIBER",
)  # codingrules 8.6's closed list, in its order.


def test_model_slot_has_exactly_the_nine_slots_codingrules_names() -> None:
    assert tuple(member.name for member in ModelSlot) == EXPECTED_SLOTS


@pytest.mark.parametrize("slot", list(ModelSlot))
def test_model_slot_value_is_its_name_and_fits_the_wire_pattern(slot: ModelSlot) -> None:
    assert slot.value == slot.name
    assert re.fullmatch(SLOT_PATTERN, slot.to_wire())


@pytest.mark.parametrize("slot", list(ModelSlot))
def test_model_slot_round_trips_through_the_wire_label(slot: ModelSlot) -> None:
    assert ModelSlot.from_wire(slot.to_wire()) is slot


@pytest.mark.parametrize("slot", list(ModelSlot))
def test_model_slot_round_trips_through_the_manifest_key(slot: ModelSlot) -> None:
    assert slot.manifest_key == slot.name.lower()
    assert ModelSlot.from_manifest_key(slot.manifest_key) is slot


@pytest.mark.parametrize("key", ["local_worker", "Worker", "", "queen "])
def test_model_slot_from_manifest_key_rejects_a_named_binding_or_a_typo(key: str) -> None:
    with pytest.raises(KeyError, match="not a model slot"):
        ModelSlot.from_manifest_key(key)


def test_model_slot_from_wire_rejects_an_unknown_label() -> None:
    with pytest.raises(ValueError, match="NOT_A_SLOT"):
        ModelSlot.from_wire("NOT_A_SLOT")


def test_effort_mirrors_the_wire_enum_member_for_member() -> None:
    assert [member.name for member in Effort] == [member.name for member in WireEffort]
    assert [member.value for member in Effort] == [member.value for member in WireEffort]


@pytest.mark.parametrize("effort", list(Effort))
def test_effort_round_trips_through_the_wire_form(effort: Effort) -> None:
    wire = effort.to_wire()

    assert isinstance(wire, WireEffort)
    assert Effort.from_wire(wire) is effort
