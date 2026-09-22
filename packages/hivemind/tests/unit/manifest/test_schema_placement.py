"""Tests for hivemind.manifest.schema.placement: [placement] and [virtual_cells].

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/placement.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.placement for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.manifest.loader import load_manifest
from hivemind.manifest.schema.placement import (
    PlacementRoleOverride,
    PlacementSection,
    VirtualCellsOverwinterSection,
    VirtualCellsSection,
)

# The repository root, five parents up from this test file, matching test_loader.py's own pattern.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"


def test_placement_section_defaults_match_v0_behaviour() -> None:
    section = PlacementSection()

    assert section.prefer == "real"
    assert section.allow_hive_stand is True
    assert section.roles == {}


def test_placement_section_rejects_an_unknown_role_key() -> None:
    with pytest.raises(ValidationError, match="unknown role keys"):
        PlacementSection(roles={"not_a_role": PlacementRoleOverride(prefer="virtual")})


def test_placement_section_accepts_a_valid_role_override() -> None:
    section = PlacementSection(roles={"drone": PlacementRoleOverride(prefer="virtual")})

    assert section.roles["drone"].prefer == "virtual"


def test_virtual_cells_section_backend_is_unset_by_default() -> None:
    """Backend unset means no Virtual side is configured (roadmap step 5.7's own words)."""
    section = VirtualCellsSection()

    assert section.backend is None
    assert section.default_image == "base-ubuntu"
    assert section.overwinter == VirtualCellsOverwinterSection()


def test_virtual_cells_section_rejects_an_unknown_backend() -> None:
    with pytest.raises(ValidationError):
        VirtualCellsSection(backend="not-a-backend")


def test_virtual_cells_section_listener_fields_default_to_loopback() -> None:
    """Roadmap step 5.6: CellListener binds loopback by default, like WebSocketServer."""
    section = VirtualCellsSection()

    assert section.listen_host == "127.0.0.1"
    assert section.listen_port == 0
    assert section.advertise_url is None


def test_virtual_cells_section_accepts_an_advertise_url_for_a_docker_gateway() -> None:
    section = VirtualCellsSection(advertise_url="ws://host.docker.internal:9500")

    assert section.advertise_url == "ws://host.docker.internal:9500"


def test_virtual_cells_section_rejects_an_out_of_range_port() -> None:
    with pytest.raises(ValidationError):
        VirtualCellsSection(listen_port=70000)


def test_virtual_cells_section_snapshot_fields_default_to_positive_values() -> None:
    """Roadmap step 5.10: snapshot_retention_s/snapshot_disk_budget_mb default to a sane window."""
    section = VirtualCellsSection()

    assert section.snapshot_retention_s == 3600.0
    assert section.snapshot_disk_budget_mb == 4096


def test_virtual_cells_section_snapshot_fields_are_overridable() -> None:
    section = VirtualCellsSection(snapshot_retention_s=120.0, snapshot_disk_budget_mb=1024)

    assert section.snapshot_retention_s == 120.0
    assert section.snapshot_disk_budget_mb == 1024


def test_virtual_cells_section_rejects_a_non_positive_snapshot_retention() -> None:
    with pytest.raises(ValidationError):
        VirtualCellsSection(snapshot_retention_s=0.0)


def test_virtual_cells_section_rejects_a_non_positive_snapshot_disk_budget() -> None:
    with pytest.raises(ValidationError):
        VirtualCellsSection(snapshot_disk_budget_mb=0)


def test_virtual_cells_overwinter_section_defaults_are_positive() -> None:
    section = VirtualCellsOverwinterSection()

    assert section.enabled is True
    assert section.max_cells > 0
    assert section.max_per_image > 0
    assert section.max_dormant_s > 0
    assert section.disk_budget_mb > 0


@pytest.mark.parametrize("filename", ["minimal.toml", "local.toml"])
def test_existing_example_manifests_omit_placement_and_virtual_cells_and_load_unchanged(
    filename: str,
) -> None:
    """Every pre-5.7 example manifest behaves exactly as before: real-only, no Virtual side."""
    manifest = load_manifest(_MANIFESTS_DIR / filename)

    assert manifest.placement.prefer == "real"
    assert manifest.placement.allow_hive_stand is True
    assert manifest.virtual_cells.backend is None


def test_full_toml_sets_the_snapshot_retention_and_budget_fields() -> None:
    """Roadmap step 5.10: docs/manifests/full.toml names both fields explicitly."""
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")

    assert manifest.virtual_cells.snapshot_retention_s == 3600.0
    assert manifest.virtual_cells.snapshot_disk_budget_mb == 4096
