"""Tests for hivemind.manifest.schema.exoskeleton: ExoskeletonSection.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/exoskeleton.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.exoskeleton for the module under test.
    - tests/unit/cli/compose/test_exoskeleton.py for the check that these defaults match attach's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.manifest import load_manifest
from hivemind.manifest.schema import ExoskeletonSection

# The repository root is this file's seventh ancestor (parents[6]): it sits in
# packages/hivemind/tests/unit/manifest/schema/.
_MANIFESTS_DIR = Path(__file__).resolve().parents[6] / "docs" / "manifests"


def test_exoskeleton_section_has_sensible_defaults() -> None:
    section = ExoskeletonSection()

    assert (section.screen_width, section.screen_height) == (1280, 800)
    assert section.ready_timeout_s == 30.0
    assert section.recording_retention_days == 30


@pytest.mark.parametrize(
    "field",
    [
        {"screen_width": 319},
        {"screen_height": 8_193},
        {"ready_timeout_s": 0},
        {"recording_retention_days": 0},
        {"browser_sandbox": False},  # Not a setting: the composition root decides it.
    ],
)
def test_exoskeleton_section_rejects_out_of_range_or_unknown_fields(
    field: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ExoskeletonSection.model_validate(field)


def test_exoskeleton_section_round_trips_through_its_own_dump() -> None:
    section = ExoskeletonSection(screen_width=1920, screen_height=1080, recording_retention_days=7)

    assert ExoskeletonSection.model_validate(section.model_dump(mode="json")) == section


def test_a_manifest_without_the_section_gets_the_defaults() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")

    assert manifest.exoskeleton == ExoskeletonSection()


def test_full_toml_names_every_exoskeleton_field() -> None:
    """docs/manifests/full.toml documents every field, so none of them is set only by default."""
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")

    assert manifest.exoskeleton.model_fields_set == set(ExoskeletonSection.model_fields)
