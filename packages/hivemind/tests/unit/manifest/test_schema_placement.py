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


def test_virtual_cells_section_exoskeleton_image_defaults_to_the_desktop_image() -> None:
    """Roadmap step 6.12: an Exoskeleton need boots images/desktop-ubuntu, never base-ubuntu."""
    section = VirtualCellsSection()

    assert section.exoskeleton_image == "desktop-ubuntu"
    assert section.exoskeleton_image != section.default_image


def test_virtual_cells_section_exoskeleton_image_is_overridable() -> None:
    section = VirtualCellsSection(exoskeleton_image="hivemind/desktop-ubuntu:dev")

    assert section.exoskeleton_image == "hivemind/desktop-ubuntu:dev"


def test_virtual_cells_section_rejects_an_empty_exoskeleton_image() -> None:
    with pytest.raises(ValidationError):
        VirtualCellsSection(exoskeleton_image="")


def test_full_toml_names_the_exoskeleton_image() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")

    assert manifest.virtual_cells.exoskeleton_image == "desktop-ubuntu"


def test_virtual_cells_section_network_policy_defaults_to_egress_only() -> None:
    """A Cell must dial the Queen through the host gateway; "none" cannot on Docker Desktop."""
    assert VirtualCellsSection().network_policy == "egress_only"
    assert VirtualCellsSection(network_policy="none").network_policy == "none"


def test_virtual_cells_section_root_filesystem_is_writable_by_default() -> None:
    """A Virtual Cell is AccessLevel.FULL: only an explicit true narrows it to scratch and /tmp."""
    assert VirtualCellsSection().read_only_rootfs is False
    assert VirtualCellsSection(read_only_rootfs=True).read_only_rootfs is True


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


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 10.6a: control_subnet, the Docker control network a Cell's link rides.
# ──────────────────────────────────────────────────────────────────────────────


def test_a_control_subnet_holds_the_listener_on_its_gateway() -> None:
    section = VirtualCellsSection(
        backend="docker",
        control_subnet="10.213.7.0/24",
        listen_host="10.213.7.1",
        listen_port=8710,
        advertise_url="ws://10.213.7.1:8710",
    )

    assert section.control_gateway == "10.213.7.1"


def test_no_control_subnet_names_no_gateway() -> None:
    assert VirtualCellsSection(backend="docker").control_gateway is None


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"backend": "qemu"}, "Docker's alone"),
        ({"control_subnet": "8.8.8.0/24", "listen_host": "8.8.8.1"}, "private IPv4"),
        ({"control_subnet": "10.213.7.0/30", "listen_host": "10.213.7.1"}, "smaller than"),
        ({"listen_host": "172.17.0.1"}, "listen_host must be the control gateway"),
        ({"listen_host": "0.0.0.0"}, "listen_host must be the control gateway"),  # noqa: S104  # SAFETY: a refused value, never bound.
        ({"advertise_url": "ws://host.docker.internal:8710"}, "advertise_url must name"),
    ],
)
def test_a_control_subnet_the_cells_could_not_use_is_refused(
    fields: dict[str, object], reason: str
) -> None:
    # A wrong backend or range, or a listener or URL a Cell cannot reach once its egress is cut.
    values: dict[str, object] = {
        "backend": "docker",
        "control_subnet": "10.213.7.0/24",
        "listen_host": "10.213.7.1",
    }
    values.update(fields)

    with pytest.raises(ValidationError, match=reason):
        VirtualCellsSection.model_validate(values)
