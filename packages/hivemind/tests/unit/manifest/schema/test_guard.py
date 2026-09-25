"""Tests for hivemind.manifest.schema.guard: GuardSection and GuardRoleSection.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/guard.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.guard for the module under test.
    - tests/unit/guard/policy/test_defaults.py for the checks guard makes on these same tables.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.guard import DEFAULT_DIRE_PATTERNS, GuardRoleSection, GuardSection


def test_guard_section_defaults_to_the_shipped_policy_unchanged() -> None:
    section = GuardSection()

    # An empty policy_file means the policy shipped in hivemind.guard.defaults.
    assert section.policy_file == ""
    assert section.deny == ()
    assert section.roles == {}
    assert section.escalation == {}


def test_guard_section_round_trips_through_json() -> None:
    section = GuardSection(
        policy_file="guard/policy.toml",
        deny=("exoskeleton:real_display",),
        roles={
            "drone": GuardRoleSection(allow=("fs:write:{scratch}/**", "tool:*")),
            "device": GuardRoleSection(allow=("observe",), proposed=("observe",)),
        },
        escalation={"tool_invocation": "alarm"},
    )

    assert GuardSection.model_validate_json(section.model_dump_json()) == section


def test_a_role_table_keeps_the_policys_proposed_list_unless_it_gives_one() -> None:
    assert GuardRoleSection(allow=()).proposed is None


@pytest.mark.parametrize("entry", ["", "fs:write:/My Files/**", "tool:\tx", "observe\n"])
def test_a_capability_entry_with_whitespace_or_nothing_in_it_is_refused(entry: str) -> None:
    with pytest.raises(ValidationError):
        GuardSection(deny=(entry,))
    with pytest.raises(ValidationError):
        GuardRoleSection(allow=(entry,))


def test_a_role_table_must_give_its_allow_list() -> None:
    with pytest.raises(ValidationError):
        GuardRoleSection.model_validate({"proposed": ["observe"]})


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError, match="extra"):
        GuardSection.model_validate({"allow": ["observe"]})


def test_dire_patterns_default_to_the_shipped_correlation_and_forgery_rules() -> None:
    # Roadmap steps 10.6 and 10.6a: the Queen acts by rule only on the keys listed here.
    shipped = ("injection_then_denial", "envelope_forgery", "segment_forgery", "subject_forgery")
    assert GuardSection().dire_patterns == DEFAULT_DIRE_PATTERNS == shipped
    assert GuardSection(dire_patterns=()).dire_patterns == ()


@pytest.mark.parametrize("key", ["Injection", "two words", "", "x" * 65])
def test_a_dire_pattern_is_a_rule_key_or_it_is_refused(key: str) -> None:
    with pytest.raises(ValidationError):
        GuardSection(dire_patterns=(key,))
