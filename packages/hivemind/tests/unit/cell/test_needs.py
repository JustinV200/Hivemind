"""Tests for hivemind.cell.needs: Isolation, OsFamily, TaskNeeds.

Fits into the Hive:
    Mirrors src/hivemind/cell/needs.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.needs for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cell.needs import MAX_NETWORK_SCOPES, MAX_SCOPE_CHARS, Isolation, OsFamily, TaskNeeds
from hivemind.cell.tiers import CombShieldLevel
from hivemind.forage.tempo import AccuracyBar, Tempo
from waggle.messages import OsFamily as WireOsFamily


def test_os_family_mirrors_the_wire_enum_member_for_member() -> None:
    assert [member.name for member in OsFamily] == [member.name for member in WireOsFamily]
    assert [member.value for member in OsFamily] == [member.value for member in WireOsFamily]


def test_task_needs_defaults_to_the_plain_case() -> None:
    needs = TaskNeeds()

    assert needs.isolation is Isolation.PREFERRED
    assert needs.exoskeleton is False
    assert needs.os is None
    assert needs.network_scopes == ()
    assert needs.disposable is True
    assert needs.comb_shield is CombShieldLevel.MEADOW
    assert needs.tempo == Tempo()


def test_task_needs_accepts_night_veil_with_required_isolation() -> None:
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)

    assert needs.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert needs.isolation is Isolation.REQUIRED


@pytest.mark.parametrize("isolation", [Isolation.PREFERRED, Isolation.NONE])
def test_task_needs_rejects_night_veil_without_required_isolation(isolation: Isolation) -> None:
    with pytest.raises(ValidationError, match="NIGHT_VEIL"):
        TaskNeeds(isolation=isolation, comb_shield=CombShieldLevel.NIGHT_VEIL)


def test_task_needs_rejects_too_many_network_scopes() -> None:
    scopes = tuple(f"host{i}.example.com" for i in range(MAX_NETWORK_SCOPES + 1))

    with pytest.raises(ValidationError):
        TaskNeeds(network_scopes=scopes)


def test_task_needs_accepts_the_maximum_number_of_network_scopes() -> None:
    scopes = tuple(f"host{i}.example.com" for i in range(MAX_NETWORK_SCOPES))

    needs = TaskNeeds(network_scopes=scopes)

    assert len(needs.network_scopes) == MAX_NETWORK_SCOPES


def test_task_needs_rejects_a_network_scope_over_the_char_limit() -> None:
    scope = "h" * (MAX_SCOPE_CHARS + 1)

    with pytest.raises(ValidationError):
        TaskNeeds(network_scopes=(scope,))


def test_task_needs_accepts_a_network_scope_at_the_char_limit() -> None:
    scope = "h" * MAX_SCOPE_CHARS

    needs = TaskNeeds(network_scopes=(scope,))

    assert needs.network_scopes == (scope,)


def test_task_needs_rejects_an_empty_network_scope() -> None:
    with pytest.raises(ValidationError):
        TaskNeeds(network_scopes=("",))


def test_task_needs_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        TaskNeeds.model_validate({"disposable": False, "extra": "nope"})


def test_task_needs_is_frozen() -> None:
    needs = TaskNeeds()

    with pytest.raises(ValidationError, match="frozen"):
        needs.disposable = False  # type: ignore[misc]  # The assignment is the test.


def test_task_needs_json_round_trips() -> None:
    original = TaskNeeds(
        isolation=Isolation.REQUIRED,
        exoskeleton=True,
        os=OsFamily.LINUX,
        network_scopes=("example.com",),
        disposable=False,
        comb_shield=CombShieldLevel.PROPOLIS,
        tempo=Tempo(latency_budget_s=5.0, accuracy=AccuracyBar.HIGH),
    )

    restored = TaskNeeds.model_validate_json(original.model_dump_json())

    assert restored == original
