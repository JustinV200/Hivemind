"""Tests for hivemind.manifest.schema.security: SecuritySection and HoneyClearanceSection.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/security.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.security for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.security import HoneyClearanceSection, HoneySection, SecuritySection
from waggle.messages import CombShieldLevel, HoneyClearance


def test_security_section_defaults_to_meadow() -> None:
    section = SecuritySection()

    assert section.default_comb_shield is CombShieldLevel.MEADOW


def test_security_section_default_tiers_cover_all_three() -> None:
    section = SecuritySection()

    assert set(section.tiers) == set(CombShieldLevel)
    assert section.tiers[CombShieldLevel.MEADOW].egress_profile == "open"
    assert section.tiers[CombShieldLevel.PROPOLIS].egress_profile == "vpn_only"
    assert section.tiers[CombShieldLevel.NIGHT_VEIL].egress_profile == "vpn_tor"
    assert section.tiers[CombShieldLevel.NIGHT_VEIL].control_channel == "tor_hidden_service"


def test_security_section_is_frozen_and_forbids_extras() -> None:
    section = SecuritySection()

    with pytest.raises(ValidationError, match="frozen"):
        section.default_comb_shield = CombShieldLevel.PROPOLIS  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        SecuritySection.model_validate({**section.model_dump(), "nope": 1})


def test_honey_clearance_section_default_matrix_covers_all_three_tiers() -> None:
    section = HoneyClearanceSection()

    assert set(section.matrix) == set(CombShieldLevel)
    assert HoneyClearance.C2 in section.matrix[CombShieldLevel.MEADOW].read
    assert HoneyClearance.C2 not in section.matrix[CombShieldLevel.NIGHT_VEIL].read
    assert HoneyClearance.C2 not in section.matrix[CombShieldLevel.NIGHT_VEIL].write


def test_honey_clearance_section_rejects_c2_read_on_night_veil() -> None:
    with pytest.raises(ValidationError, match="NIGHT_VEIL"):
        HoneyClearanceSection(
            matrix={
                CombShieldLevel.NIGHT_VEIL: {
                    "read": [HoneyClearance.C0, HoneyClearance.C2],
                    "write": [HoneyClearance.C0],
                }
            }
        )


def test_honey_clearance_section_rejects_c2_write_on_night_veil() -> None:
    with pytest.raises(ValidationError, match="NIGHT_VEIL"):
        HoneyClearanceSection(
            matrix={
                CombShieldLevel.NIGHT_VEIL: {
                    "read": [HoneyClearance.C0],
                    "write": [HoneyClearance.C0, HoneyClearance.C2],
                }
            }
        )


def test_honey_clearance_section_accepts_c1_on_night_veil() -> None:
    section = HoneyClearanceSection(
        matrix={
            CombShieldLevel.NIGHT_VEIL: {
                "read": [HoneyClearance.C0, HoneyClearance.C1],
                "write": [HoneyClearance.C0, HoneyClearance.C1],
            }
        }
    )

    assert HoneyClearance.C1 in section.matrix[CombShieldLevel.NIGHT_VEIL].read


def test_honey_section_wraps_clearance() -> None:
    honey = HoneySection()

    assert isinstance(honey.clearance, HoneyClearanceSection)
