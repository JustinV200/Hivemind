"""Tests for hivemind.manifest.schema.supervision: SupervisionSection and MemorySection.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/supervision.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.supervision for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.supervision import (
    DEFAULT_WAX_TEXT_CAP_CHARS,
    MemorySection,
    SupervisionSection,
)
from waggle.messages.cell.wax import MAX_WAX_TEXT_CHARS


def test_supervision_section_has_sensible_defaults() -> None:
    section = SupervisionSection()

    # Both unset by default: a relative path default could only ever be right for a manifest
    # in one particular directory, so unset means the table shipped in
    # hivemind.supervision.defaults instead.
    assert section.policy_file is None
    assert section.capping_tiers_file is None
    assert section.heartbeat_interval_s == 5.0
    assert section.heartbeat_miss_limit == 3
    assert section.max_offline_s == 600.0
    assert section.alarm_attempt_limit == 3


def test_supervision_section_rejects_a_non_positive_heartbeat_interval() -> None:
    with pytest.raises(ValidationError):
        SupervisionSection(heartbeat_interval_s=0)


def test_memory_section_has_sensible_defaults() -> None:
    section = MemorySection()

    assert section.budget_fraction == 0.6
    assert section.handoff_threshold == 0.66
    assert section.hot_window_s == 4.0 * 3600.0
    assert section.pins == ()
    assert section.sweep_interval_s == 3_600.0
    assert section.cell_wax_cap == 20
    assert section.wax_text_cap_chars == DEFAULT_WAX_TEXT_CAP_CHARS


def test_memory_section_wax_text_cap_chars_matches_waggles_own_ceiling() -> None:
    """The manifest's own default never silently drifts from the wire shape's hard ceiling."""
    assert DEFAULT_WAX_TEXT_CAP_CHARS == MAX_WAX_TEXT_CHARS


def test_memory_section_rejects_a_non_positive_wax_text_cap_chars() -> None:
    with pytest.raises(ValidationError):
        MemorySection(wax_text_cap_chars=0)


def test_memory_section_accepts_a_wax_text_cap_chars_lower_than_the_wire_ceiling() -> None:
    assert MemorySection(wax_text_cap_chars=500).wax_text_cap_chars == 500


def test_memory_section_rejects_a_non_positive_sweep_interval() -> None:
    with pytest.raises(ValidationError):
        MemorySection(sweep_interval_s=0)


def test_memory_section_accepts_a_custom_sweep_interval() -> None:
    assert MemorySection(sweep_interval_s=60.0).sweep_interval_s == 60.0


def test_memory_section_rejects_a_non_positive_hot_window() -> None:
    with pytest.raises(ValidationError):
        MemorySection(hot_window_s=0)


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1])
def test_memory_section_rejects_a_budget_fraction_outside_zero_to_one(fraction: float) -> None:
    with pytest.raises(ValidationError):
        MemorySection(budget_fraction=fraction)


def test_memory_section_accepts_a_budget_fraction_of_exactly_one() -> None:
    assert MemorySection(budget_fraction=1.0).budget_fraction == 1.0


def test_memory_section_is_frozen_and_forbids_extras() -> None:
    section = MemorySection()

    with pytest.raises(ValidationError, match="frozen"):
        section.item_cap_chars = 1  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        MemorySection.model_validate({**section.model_dump(), "nope": 1})
