"""Tests for hivemind.supervision.policy: PolicyRule, EscalationPolicy, load_policy and decide.

Fits into the Hive:
    Mirrors src/hivemind/supervision/policy.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.policy for the module under test.
    - docs/supervision/default-policy.toml for the file test_load_policy_reads_the_shipped_default
      loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.supervision import make_alarm, make_policy
from pydantic import ValidationError

from hivemind.supervision.alarm import AlarmKind
from hivemind.supervision.errors import PolicyError
from hivemind.supervision.policy import (
    PolicyAction,
    PolicyRule,
    decide,
    load_policy,
)

# The repository root, five parents up from this test file
# (packages/hivemind/tests/unit/supervision/test_policy.py).
_REPO_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_POLICY_PATH = _REPO_ROOT / "docs" / "supervision" / "default-policy.toml"


def test_load_policy_reads_the_shipped_default() -> None:
    policy = load_policy(_DEFAULT_POLICY_PATH)

    assert policy.default is PolicyAction.ESCALATE
    assert len(policy.rules) > 0


def test_load_policy_raises_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="Could not read"):
        load_policy(tmp_path / "does-not-exist.toml")


def test_load_policy_raises_for_invalid_toml(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("this is not [ valid toml", encoding="utf-8")

    with pytest.raises(PolicyError, match="Could not read"):
        load_policy(bad_file)


def test_load_policy_raises_when_the_document_fails_validation(tmp_path: Path) -> None:
    bad_file = tmp_path / "missing-default.toml"
    bad_file.write_text(
        '[[rules]]\nkind = "WORKER_FAILED"\nmin_attempts = 1\naction = "RETRY"\n', encoding="utf-8"
    )

    with pytest.raises(PolicyError, match="invalid"):
        load_policy(bad_file)


def test_policy_rule_rejects_a_min_attempts_below_one() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        PolicyRule(kind=None, min_attempts=0, action=PolicyAction.ESCALATE)


def test_escalation_policy_is_frozen() -> None:
    policy = make_policy()

    with pytest.raises(ValidationError, match="frozen"):
        policy.default = PolicyAction.CANCEL  # type: ignore[misc]  # The assignment is the test.


# (kind, attempts) -> expected action, against the fixed policy built by builders.make_policy:
#   WORKER_FAILED: 1 -> RETRY, 2 -> ESCALATE (kind-specific rows)
#   any other kind: wildcard row at min_attempts=1 -> RESPAWN
#   attempts == 0: no row anywhere has min_attempts <= 0, so the policy's own default (ESCALATE)
_DECIDE_CASES = [
    (AlarmKind.WORKER_FAILED, 0, PolicyAction.ESCALATE),  # falls through to default
    (AlarmKind.WORKER_FAILED, 1, PolicyAction.RETRY),
    (AlarmKind.WORKER_FAILED, 2, PolicyAction.ESCALATE),
    (AlarmKind.WORKER_FAILED, 5, PolicyAction.ESCALATE),  # highest matching min_attempts still wins
    (AlarmKind.WORKER_CRASHED, 1, PolicyAction.RESPAWN),  # no WORKER_CRASHED row: wildcard applies
    (AlarmKind.WORKER_CRASHED, 0, PolicyAction.ESCALATE),  # wildcard also requires attempts >= 1
]


@pytest.mark.parametrize(("kind", "attempts", "expected"), _DECIDE_CASES)
def test_decide_matches_the_expected_action(
    kind: AlarmKind, attempts: int, expected: PolicyAction
) -> None:
    policy = make_policy()
    alarm = make_alarm(kind=kind, attempts=attempts)

    assert decide(policy, alarm) is expected


def test_decide_prefers_kind_specific_rows_over_the_wildcard() -> None:
    policy = make_policy(
        rules=(
            PolicyRule(kind=None, min_attempts=1, action=PolicyAction.CANCEL),
            PolicyRule(kind=AlarmKind.WORKER_FAILED, min_attempts=1, action=PolicyAction.RETRY),
        )
    )
    alarm = make_alarm(kind=AlarmKind.WORKER_FAILED, attempts=1)

    assert decide(policy, alarm) is PolicyAction.RETRY


def test_decide_falls_back_to_default_when_no_row_matches_at_all() -> None:
    policy = make_policy(rules=())
    alarm = make_alarm(attempts=10)

    assert decide(policy, alarm) is policy.default


@pytest.mark.parametrize(
    ("kind", "attempts", "action"),
    [
        (AlarmKind.WORKER_FAILED, 1, PolicyAction.RETRY),
        (AlarmKind.WORKER_FAILED, 2, PolicyAction.REBIND),
        (AlarmKind.WORKER_FAILED, 3, PolicyAction.ESCALATE),
        (AlarmKind.WORKER_CRASHED, 1, PolicyAction.RESPAWN),
        (AlarmKind.WORKER_CRASHED, 3, PolicyAction.ESCALATE),
        (AlarmKind.WORKER_STALLED, 1, PolicyAction.RESPAWN),
        (AlarmKind.WORKER_STALLED, 3, PolicyAction.ESCALATE),
        (AlarmKind.CONTEXT_OVERFLOW, 1, PolicyAction.RESPAWN),
        (AlarmKind.POSTCONDITION_FAILED, 1, PolicyAction.RETRY),
        (AlarmKind.POSTCONDITION_FAILED, 2, PolicyAction.ESCALATE),
        (AlarmKind.ACCEPTANCE_FAILED, 1, PolicyAction.RETRY),
        (AlarmKind.ACCEPTANCE_FAILED, 2, PolicyAction.ESCALATE),
        (AlarmKind.GRANT_EXCEEDED, 1, PolicyAction.ESCALATE),
        (AlarmKind.PROVIDER_UNAVAILABLE, 1, PolicyAction.REBIND),
        (AlarmKind.PROVIDER_UNAVAILABLE, 2, PolicyAction.ESCALATE),
        (AlarmKind.QUOTA_EXCEEDED, 1, PolicyAction.CANCEL),
        (AlarmKind.AUDIT_FAILED, 5, PolicyAction.ESCALATE),  # no row: always the default
        (AlarmKind.CELL_UNREACHABLE, 5, PolicyAction.ESCALATE),  # no row: always the default
    ],
)
def test_decide_matches_the_shipped_default_policy(
    kind: AlarmKind, attempts: int, action: PolicyAction
) -> None:
    policy = load_policy(_DEFAULT_POLICY_PATH)
    alarm = make_alarm(kind=kind, attempts=attempts)

    assert decide(policy, alarm) is action
