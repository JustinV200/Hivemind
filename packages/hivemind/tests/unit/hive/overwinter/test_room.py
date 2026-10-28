"""Tests for hivemind.hive.overwinter.room: which dormant Cells make room for a fresher one.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/overwinter/room.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.overwinter.room for make_room, the function under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hivemind.hive.overwinter.policy import OverwinterConfig
from hivemind.hive.overwinter.room import Occupant, make_room
from waggle.ids import CellId

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_A, _B, _C = CellId("cell_a"), CellId("cell_b"), CellId("cell_c")


def _occupants() -> list[Occupant]:
    """Three dormant Cells, oldest first: A (other image), B and C (base), 10 MB each."""
    return [
        Occupant(_A, "desktop", 10, _T0),
        Occupant(_B, "base", 10, _T0 + timedelta(minutes=1)),
        Occupant(_C, "base", 10, _T0 + timedelta(minutes=2)),
    ]


def _config(**overrides: int) -> OverwinterConfig:
    """Bounds the three occupants fit inside, overridable per case."""
    fields = {"max_cells": 4, "max_per_image": 3, "disk_budget_mb": 100} | overrides
    return OverwinterConfig(enabled=True, max_dormant_s=60.0, **fields)


@pytest.mark.parametrize(
    ("image", "disk_mb", "bounds", "expected"),
    [
        # Room already: nothing goes.
        ("base", 10, {}, ()),
        # The pool is full in total: the oldest Cell goes, whatever its image.
        ("base", 10, {"max_cells": 3}, (_A,)),
        # The image is full: only a Cell of that image frees its slot, the oldest of them.
        ("base", 10, {"max_per_image": 2}, (_B,)),
        # The disk budget needs two to go for a 30 MB Cell: the two oldest.
        ("base", 30, {"disk_budget_mb": 40}, (_A, _B)),
        # Bigger than the whole budget: no eviction could help, so none happens.
        ("base", 200, {}, ()),
    ],
)
def test_make_room_evicts_the_oldest_cells_that_help_or_none(
    image: str, disk_mb: int, bounds: dict[str, int], expected: tuple[CellId, ...]
) -> None:
    victims = make_room(_occupants(), image, disk_mb, _config(**bounds))

    assert victims == expected
