"""Tests for `[guard.untrusted_content]` in hivemind.manifest.schema.security.guard (roadmap 10.6b).

The scanner's thresholds per Comb Shield tier and its input bound: documented defaults, a drop
threshold never below its label threshold, and a stricter tier never laxer than a looser one.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/security/guard.py (codingrules section 3), split by
    feature from test_guard.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.security.guard for ScanThresholds and UntrustedContentSection.
    - docs/manifests/full.toml for the written-out example this test also loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.manifest import load_manifest
from hivemind.manifest.schema.security.guard import (
    DEFAULT_MAX_SCAN_CHARS,
    GuardSection,
    ScanThresholds,
    UntrustedContentSection,
)

# packages/hivemind/tests/unit/manifest/schema/security/ -> parents[7] is the repository root.
_FULL_TOML = Path(__file__).resolve().parents[7] / "docs" / "manifests" / "full.toml"


def test_the_defaults_are_the_documented_ones_and_tighten_with_the_tier() -> None:
    section = GuardSection().untrusted_content

    assert section.max_scan_chars == DEFAULT_MAX_SCAN_CHARS
    assert (section.meadow.label, section.meadow.drop) == (3.0, 7.0)
    assert (section.propolis.label, section.propolis.drop) == (2.5, 6.0)
    assert (section.night_veil.label, section.night_veil.drop) == (2.0, 5.0)


def test_a_drop_threshold_below_its_label_threshold_is_refused() -> None:
    with pytest.raises(ValidationError, match="drop"):
        ScanThresholds(label=5.0, drop=4.0)


def test_a_stricter_tier_may_not_be_laxer_than_a_looser_one() -> None:
    lax_night_veil = ScanThresholds(label=9.0, drop=12.0)

    with pytest.raises(ValidationError, match="night_veil"):
        UntrustedContentSection(night_veil=lax_night_veil)


def test_the_input_bound_is_itself_bounded() -> None:
    with pytest.raises(ValidationError):
        UntrustedContentSection(max_scan_chars=10)
    with pytest.raises(ValidationError):
        UntrustedContentSection(max_scan_chars=10_000_000)


def test_the_section_round_trips_through_json() -> None:
    section = UntrustedContentSection(
        max_scan_chars=4_096, meadow=ScanThresholds(label=4.0, drop=9.0)
    )

    assert UntrustedContentSection.model_validate_json(section.model_dump_json()) == section


def test_full_toml_writes_the_table_out_with_its_defaults() -> None:
    manifest = load_manifest(_FULL_TOML)

    assert manifest.guard.untrusted_content == UntrustedContentSection()
