"""Tests for hivemind.manifest.schema.nurse: NurseSection.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/nurse.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.nurse for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.manifest import load_manifest
from hivemind.manifest.schema import NurseSection
from hivemind.manifest.schema.nurse import MAX_EVIDENCE_CHARS
from hivemind.supervision.capping.checks import MAX_NURSE_EVIDENCE_CHARS
from waggle.messages.task import WorkerRole

# The repository root is this file's seventh ancestor: packages/hivemind/tests/unit/manifest/schema.
_MANIFESTS_DIR = Path(__file__).resolve().parents[6] / "docs" / "manifests"


def test_the_evidence_ceiling_mirrors_the_seams() -> None:
    # Layer 1 cannot import the seam's constant, so this holds the two in step.
    assert MAX_EVIDENCE_CHARS == MAX_NURSE_EVIDENCE_CHARS


@pytest.mark.parametrize(
    "fields",
    [
        {"skip_roles": ["UNDERTAKER", "JANITOR"]},  # A typo must not quietly skip nothing.
        {"max_rounds": 0},
        {"max_rounds": 8},  # The wire's review_round stops at 7.
        {"max_evidence_chars": MAX_EVIDENCE_CHARS + 1},
        {"tempo_opt_out": True},  # Deliberately not a setting (ADR-0061).
    ],
)
def test_nurse_section_refuses_bad_or_unknown_fields(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NurseSection.model_validate(fields)


def test_the_full_example_loads_its_nurse_section() -> None:
    nurse = load_manifest(_MANIFESTS_DIR / "full.toml").nurse

    assert nurse.enabled
    assert nurse.skipped() == frozenset({WorkerRole.UNDERTAKER, WorkerRole.HOUSE_BEE})
