"""Tests for hivemind.forage.tempo: AccuracyBar and Tempo.

Fits into the Hive:
    Mirrors src/hivemind/forage/tempo.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.tempo for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.forage.tempo import AccuracyBar, Tempo
from waggle.messages import AccuracyBar as WireAccuracyBar
from waggle.messages import Tempo as WireTempo


def test_accuracy_bar_mirrors_the_wire_enum_member_for_member() -> None:
    hivemind_names = [member.name for member in AccuracyBar]
    wire_names = [member.name for member in WireAccuracyBar]

    assert hivemind_names == wire_names
    assert [member.value for member in AccuracyBar] == [member.value for member in WireAccuracyBar]


def test_tempo_defaults_to_no_budget_and_normal_accuracy() -> None:
    tempo = Tempo()

    assert tempo.latency_budget_s is None
    assert tempo.accuracy is AccuracyBar.NORMAL


def test_tempo_accepts_a_positive_latency_budget() -> None:
    tempo = Tempo(latency_budget_s=30.0, accuracy=AccuracyBar.HIGH)

    assert tempo.latency_budget_s == 30.0
    assert tempo.accuracy is AccuracyBar.HIGH


@pytest.mark.parametrize("budget", [0.0, -1.0])
def test_tempo_rejects_a_non_positive_latency_budget(budget: float) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        Tempo(latency_budget_s=budget)


def test_tempo_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Tempo.model_validate({"latency_budget_s": 5.0, "accuracy": "NORMAL", "extra": "nope"})


def test_tempo_is_frozen() -> None:
    tempo = Tempo()

    with pytest.raises(ValidationError, match="frozen"):
        tempo.accuracy = AccuracyBar.HIGH  # type: ignore[misc]  # The assignment is the test.


def test_tempo_from_wire_builds_the_equivalent_value() -> None:
    wire = WireTempo(latency_budget_s=12.5, accuracy=WireAccuracyBar.CRITICAL)

    tempo = Tempo.from_wire(wire)

    assert tempo.latency_budget_s == 12.5
    assert tempo.accuracy is AccuracyBar.CRITICAL


def test_tempo_to_wire_round_trips_through_from_wire() -> None:
    original = Tempo(latency_budget_s=8.0, accuracy=AccuracyBar.LOW)

    wire = original.to_wire()
    restored = Tempo.from_wire(wire)

    assert restored == original


def test_tempo_to_wire_with_no_budget_round_trips() -> None:
    original = Tempo()

    wire = original.to_wire()

    assert wire.latency_budget_s is None
    assert Tempo.from_wire(wire) == original


def test_tempo_json_round_trips() -> None:
    original = Tempo(latency_budget_s=2.5, accuracy=AccuracyBar.HIGH)

    restored = Tempo.model_validate_json(original.model_dump_json())

    assert restored == original
