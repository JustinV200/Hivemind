"""Tests for hivemind.royal_jelly.registry.principals: plain frozen values with a stable label.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/registry/principals.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.registry.principals for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.royal_jelly.registry import QueenPrincipal, WardenPrincipal
from hivemind.royal_jelly.registry.principals import QUEEN_LABEL
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_warden_id


def test_the_queen_principal_is_a_label_less_singleton_value() -> None:
    assert QueenPrincipal() == QueenPrincipal()
    assert QueenPrincipal().label == QUEEN_LABEL == "queen"


def test_a_warden_principal_labels_itself_with_its_warden_id() -> None:
    warden_id = new_warden_id(FakeClock())
    principal = WardenPrincipal(warden_id, new_cell_id(FakeClock()))

    assert principal.label == warden_id
    assert principal.label != QUEEN_LABEL


def test_principals_are_frozen() -> None:
    principal = WardenPrincipal(new_warden_id(FakeClock()), new_cell_id(FakeClock()))

    with pytest.raises(dataclasses.FrozenInstanceError):
        principal.cell_id = new_cell_id(FakeClock())  # type: ignore[misc]  # frozen on purpose


def test_warden_principals_compare_by_value() -> None:
    warden, cell = new_warden_id(FakeClock()), new_cell_id(FakeClock())

    assert WardenPrincipal(warden, cell) == WardenPrincipal(warden, cell)
    assert WardenPrincipal(warden, cell) != WardenPrincipal(warden, new_cell_id(FakeClock()))
