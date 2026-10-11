"""Tests for hivemind.cli.llm.eval.evidence: GoalEvidence, the raw data of one run.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/evidence.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.evidence for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.cli.llm.eval.evidence import GoalEvidence


def test_a_bare_evidence_has_no_tasks_events_files_or_error() -> None:
    evidence = GoalEvidence(goal="haiku", succeeded=False, timed_out=False, elapsed_s=0.0)

    assert (evidence.tasks, evidence.events, dict(evidence.files)) == ((), (), {})
    assert evidence.error is None


def test_evidence_is_immutable() -> None:
    evidence = GoalEvidence(goal="haiku", succeeded=True, timed_out=False, elapsed_s=1.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence.succeeded = False  # type: ignore[misc]
