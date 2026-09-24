"""Tests for hivemind.manifest.schema.guard's Guard Bee slice: two `[guard]` keys and `[guard.bee]`.

Roadmap step 10.6: the request floor and hourly cap, the Guard Bee's cadence, judge bound, audit
raise and rule overrides. The documented defaults, the names that must match hivemind.guard's own
enums, the refusals, and the written-out example in docs/manifests/full.toml.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/guard.py (codingrules section 3), split by feature from
    test_guard.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.guard for GuardSection, GuardBeeSection and GuardBeeRuleOverride.
    - hivemind.workers.roles.guard_bee for the Guard Bee that reads them.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from hivemind.guard import GuardAction, GuardConfidence
from hivemind.manifest import load_manifest
from hivemind.manifest.schema.guard import (
    DEFAULT_REQUEST_CONFIDENCE,
    DEFAULT_REQUESTS_PER_HOUR,
    GuardActionName,
    GuardBeeRuleOverride,
    GuardBeeSection,
    GuardConfidenceName,
    GuardSection,
)

# packages/hivemind/tests/unit/manifest/schema/ -> parents[6] is the repository root.
_FULL_TOML = Path(__file__).resolve().parents[6] / "docs" / "manifests" / "full.toml"


def test_the_defaults_are_the_documented_ones() -> None:
    section = GuardSection()

    assert section.request_confidence == DEFAULT_REQUEST_CONFIDENCE == "high"
    assert section.requests_per_hour == DEFAULT_REQUESTS_PER_HOUR
    assert section.bee == GuardBeeSection()
    assert section.bee.rules == {}


def test_the_confidence_and_action_names_are_the_enums_own_values() -> None:
    assert list(get_args(GuardConfidenceName)) == [tier.value for tier in GuardConfidence]
    assert set(get_args(GuardActionName)) == {action.value for action in GuardAction}


@pytest.mark.parametrize(
    "fields",
    [
        {"requests_per_hour": 0},
        {"request_confidence": "certain"},
        {"bee": {"interval_s": 0}},
        {"bee": {"audit_raise_step": 1.5}},
        {"bee": {"rules": {"denial_burst": {"ratio": 2.0}}}},
        {"bee": {"rules": {"denial_burst": {"action": "delete_cell"}}}},
        {"bee": {"rules": {"denial_burst": {"threshold": 3, "matchers": []}}}},
    ],
)
def test_a_malformed_value_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GuardSection.model_validate(fields)


def test_an_override_keeps_only_what_it_names() -> None:
    override = GuardBeeRuleOverride.model_validate({"threshold": 8, "confidence": "medium"})

    named = override.model_dump(exclude_none=True)

    assert named == {"threshold": 8.0, "confidence": "medium"}


def test_the_full_example_manifest_sets_every_guard_bee_key() -> None:
    guard = load_manifest(_FULL_TOML, {}).guard

    assert guard.request_confidence == "high"
    assert guard.requests_per_hour == 6
    assert guard.bee.interval_s == 5.0
    assert guard.bee.rules["denial_burst"].threshold == 8
