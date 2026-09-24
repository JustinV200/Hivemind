"""Tests for hivemind.queen.planner.location: which asks a Night Veil goal is refused for (10.3d).

Fits into the Hive:
    Mirrors src/hivemind/queen/planner/location.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.planner.location for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.cell import CombShieldLevel, Isolation, TaskNeeds
from hivemind.guard import Capability, CapabilityFamily
from hivemind.queen.planner.location import (
    NightVeilLocationError,
    ceiling_location_asks,
    needs_location_asks,
)

_NIGHT_VEIL = {"comb_shield": CombShieldLevel.NIGHT_VEIL, "isolation": Isolation.REQUIRED}


def _net(scope: str) -> Capability:
    return Capability(family=CapabilityFamily.NET, scope=scope)


def test_the_operators_own_path_carries_no_ceiling_and_so_no_ask() -> None:
    assert ceiling_location_asks(None) == ()


def test_every_location_family_and_metadata_host_in_a_ceiling_is_an_ask() -> None:
    ceiling = ("geo:*", "wifi:scan", "host:metadata", "net:169.254.169.254", "net:example.com")

    asks = ceiling_location_asks(ceiling)

    assert [str(ask) for ask in asks] == [
        "geo:*",
        "host:metadata",
        "net:169.254.169.254",
        "wifi:scan",
    ]


@pytest.mark.parametrize("held", [("net:*",), ("fs:read:**", "exec:git"), ()])
def test_a_ceiling_without_a_location_ask_asks_nothing(held: tuple[str, ...]) -> None:
    assert ceiling_location_asks(held) == ()


def test_a_night_veil_tasks_metadata_scopes_are_asks_once_each() -> None:
    needs = [
        TaskNeeds(network_scopes=("metadata.google.internal", "api.example.com"), **_NIGHT_VEIL),
        TaskNeeds(network_scopes=("metadata.google.internal",), **_NIGHT_VEIL),
    ]

    assert needs_location_asks(needs) == (_net("metadata.google.internal"),)


def test_a_task_at_another_tier_asks_nothing_here() -> None:
    needs = [TaskNeeds(network_scopes=("169.254.169.254",))]

    assert needs_location_asks(needs) == ()


def test_the_refusal_names_every_ask_and_keeps_them() -> None:
    asks = (_net("169.254.169.254"), Capability.parse("geo:*"))

    error = NightVeilLocationError(asks)

    assert "net:169.254.169.254, geo:*" in str(error)
    assert error.asks == asks
    assert error.code == "hivemind.queen.night_veil_location"
