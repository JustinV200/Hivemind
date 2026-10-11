"""Tests for hivemind.llm.routing.rules: RoutingRules and default_floors.

Fits into the Hive:
    Mirrors src/hivemind/llm/routing/rules.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.routing.rules for the module under test.
    - hivemind.manifest.schema.floors for the manifest-side defaults this must equal.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.forage import ModelSlot
from hivemind.llm.routing import RoutingRules, default_floors
from hivemind.manifest.schema.floors import complete_floors


def test_default_floors_gives_every_slot_a_floor_from_one_to_five() -> None:
    floors = default_floors()

    assert set(floors) == set(ModelSlot)
    assert all(1 <= floor <= 5 for floor in floors.values())


@pytest.mark.parametrize(
    ("slot", "floor"),
    [
        (ModelSlot.QUEEN, 5),
        (ModelSlot.ATTENDANT, 1),
        (ModelSlot.RIPENER, 1),
        (ModelSlot.EMBEDDER, 1),
        (ModelSlot.TRANSCRIBER, 1),
        (ModelSlot.WARDEN, 2),
        (ModelSlot.WORKER, 2),
        (ModelSlot.SCAFFOLDER, 2),
        (ModelSlot.JUDGE, 2),
    ],
)
def test_default_floors_match_the_documented_defaults(slot: ModelSlot, floor: int) -> None:
    assert default_floors()[slot] == floor


def test_default_floors_equal_the_manifests_completed_empty_table() -> None:
    # The two packages are independent siblings; this is the sync check their docstrings promise.
    assert dict(default_floors()) == dict(complete_floors({}))


def test_floor_for_falls_back_to_the_default_for_a_slot_the_table_omits() -> None:
    rules = RoutingRules(floors={ModelSlot.QUEEN: 4})

    assert rules.floor_for(ModelSlot.QUEEN) == 4
    assert rules.floor_for(ModelSlot.WORKER) == 2


def test_raise_floor_never_lowers_an_already_raised_floor() -> None:
    rules = RoutingRules().raise_floor(4)

    assert rules.raise_floor(2).raised_floor == 4
    assert rules.raise_floor(5).raised_floor == 5


def test_routing_rules_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        RoutingRules().offline = True  # type: ignore[misc]


def test_routing_rules_default_to_online_with_no_raised_floor_and_no_spend() -> None:
    rules = RoutingRules()

    assert (rules.offline, rules.raised_floor, rules.spend_cap_usd) == (False, 0, 0.0)
