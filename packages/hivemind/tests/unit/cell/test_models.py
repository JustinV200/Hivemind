"""Unit tests for hivemind.cell.models: CellCapabilities wire round trip, Cell's own validator."""

from __future__ import annotations

import pytest
from builders.cells import make_capabilities, make_cell
from pydantic import ValidationError

from hivemind.cell.models import CellKind
from hivemind.cell.tiers import AccessLevel, CombShieldLevel


def test_cell_capabilities_wire_round_trip_preserves_every_field() -> None:
    capabilities = make_capabilities(
        has_display=True, has_browser=True, network_scopes=("net:internet",)
    )

    platform, report = capabilities.to_wire()
    restored = capabilities.from_wire(platform, report)

    assert restored == capabilities


def test_cell_real_at_night_veil_is_rejected() -> None:
    with pytest.raises(ValidationError, match="NIGHT_VEIL"):
        make_cell(
            kind=CellKind.REAL,
            access_level=AccessLevel.SCRATCH,
            comb_shield=CombShieldLevel.NIGHT_VEIL,
        )


def test_cell_virtual_below_full_access_is_rejected() -> None:
    with pytest.raises(ValidationError, match="FULL"):
        make_cell(kind=CellKind.VIRTUAL, access_level=AccessLevel.SCRATCH)


def test_cell_virtual_at_full_access_is_accepted() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, access_level=AccessLevel.FULL)

    assert cell.access_level is AccessLevel.FULL


def test_cell_real_never_night_veil_but_meadow_is_accepted() -> None:
    cell = make_cell(kind=CellKind.REAL, comb_shield=CombShieldLevel.MEADOW)

    assert cell.comb_shield is CombShieldLevel.MEADOW
