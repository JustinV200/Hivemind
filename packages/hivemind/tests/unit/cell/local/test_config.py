"""Unit tests for hivemind.cell.local.config: HiveStandConfig.from_section.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/config.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.config for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell.local.config import HiveStandConfig
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.manifest.schema.core import HiveStandSection
from waggle.messages.labels import AccessLevel as WireAccessLevel


def test_from_section_resolves_a_relative_scratch_root_against_manifest_dir(
    tmp_path: Path,
) -> None:
    section = HiveStandSection(scratch_root=Path(".hive/scratch"))

    config = HiveStandConfig.from_section(section, tmp_path)

    assert config.scratch_root == (tmp_path / ".hive" / "scratch").resolve(strict=False)


def test_from_section_leaves_an_absolute_scratch_root_alone(tmp_path: Path) -> None:
    absolute = (tmp_path / "elsewhere" / "scratch").resolve(strict=False)
    section = HiveStandSection(scratch_root=absolute)

    config = HiveStandConfig.from_section(section, tmp_path / "manifest-dir")

    assert config.scratch_root == absolute


def test_from_section_converts_the_wire_access_level_via_the_hivemind_mirror(
    tmp_path: Path,
) -> None:
    section = HiveStandSection(access_level=WireAccessLevel.FULL)

    config = HiveStandConfig.from_section(section, tmp_path)

    assert config.access_level is AccessLevel.FULL


def test_from_section_carries_quota_and_reserve_through_unchanged(tmp_path: Path) -> None:
    section = HiveStandSection(scratch_quota_mb=2048, disk_reserve_mb=512)

    config = HiveStandConfig.from_section(section, tmp_path)

    assert config.scratch_quota_mb == 2048
    assert config.disk_reserve_mb == 512


def test_from_section_carries_capacity_overrides_through_unchanged(tmp_path: Path) -> None:
    section = HiveStandSection()
    section = section.model_copy(
        update={
            "capacity": section.capacity.model_copy(
                update={"max_sub_bees": 2, "cores": 4, "memory_bytes": 1024}
            )
        }
    )

    config = HiveStandConfig.from_section(section, tmp_path)

    assert config.max_sub_bees == 2
    assert config.cores == 4
    assert config.memory_bytes == 1024


def test_comb_shield_defaults_to_meadow() -> None:
    config = HiveStandConfig(
        enabled=True,
        scratch_root=Path("/scratch"),
        scratch_quota_mb=4096,
        disk_reserve_mb=1024,
        max_sub_bees=None,
        cores=None,
        memory_bytes=None,
        access_level=AccessLevel.SCRATCH,
    )

    assert config.comb_shield is CombShieldLevel.MEADOW
