"""Tests for waggle.messages.capping.verdict: Verdict, PostconditionResult and RollbackDone.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins VerdictOutcome's and RollbackMethod's
    members, and for the three classes construction, the rejection of an extra field, every
    bound, and every validator spec section 8.9 names (the failing check or postcondition a
    verdict's outcome implies, the snapshot id a rollback method implies, and the residue an
    incomplete rollback implies) in both directions. The family's EXAMPLES tuple and the round
    trip of these classes live in test_proposals.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.capping.verdict for the module under test.
    - test_proposals.py for EXAMPLES and the family's gate entry.
    - docs/waggle/spec.md section 8.9 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from enum import Enum

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_PATH_CHARS, WaggleMessage
from waggle.messages.capping.verdict import (
    MAX_OBSERVED_CHARS,
    MAX_POSTCONDITION_INDEX,
    MAX_RESIDUAL_PATHS,
    MAX_SNAPSHOT_ID_CHARS,
    PostconditionResult,
    RollbackDone,
    RollbackMethod,
    Verdict,
    VerdictOutcome,
)
from waggle.messages.labels import HoneyClearance, PostconditionKind

CLOCK = FakeClock()
PROPOSAL_ID = new_id(IdKind.MESSAGE, CLOCK)
TASK_ID = new_id(IdKind.TASK, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
VERDICT = Verdict(
    proposal_id=PROPOSAL_ID,
    task_id=TASK_ID,
    cell_id=CELL_ID,
    outcome=VerdictOutcome.VERIFIED,
    reason="Applied; the test passed.",
    failing_check=None,
    failing_postcondition=None,
)
POSTCONDITION_RESULT = PostconditionResult(
    proposal_id=PROPOSAL_ID,
    task_id=TASK_ID,
    cell_id=CELL_ID,
    index=0,
    kind=PostconditionKind.TEST_PASSES,
    has_held=True,
    observed="1 passed in 0.3s",
    clearance=HoneyClearance.C1,
    reason="The test passed on the first run.",
)
ROLLBACK = RollbackDone(
    proposal_id=PROPOSAL_ID,
    task_id=TASK_ID,
    cell_id=CELL_ID,
    method=RollbackMethod.REVERSE_DIFF,
    snapshot_id=None,
    is_complete=True,
    residual_paths=(),
    reason="Postcondition 0 did not hold; the diff was reversed cleanly.",
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


@pytest.mark.parametrize(
    ("enum_type", "names"),
    [
        (VerdictOutcome, ["VERIFIED", "REJECTED", "ROLLED_BACK", "CHANGES_REQUESTED"]),
        (RollbackMethod, ["SNAPSHOT", "REVERSE_DIFF", "NONE"]),
    ],
)
def test_verdict_enum_members_and_values_match_the_spec(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert all(member.value == member.name for member in enum_type)


@pytest.mark.parametrize("example", [VERDICT, POSTCONDITION_RESULT, ROLLBACK], ids=type)
def test_verdict_half_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


# ──────────────────────────────────────────────────────────────────────────────
# Verdict
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "REJECTED", "failing_check": "LINT"},
        {"outcome": "CHANGES_REQUESTED", "failing_check": "JUDGE"},
        {"outcome": "ROLLED_BACK", "failing_postcondition": MAX_POSTCONDITION_INDEX},
        {"outcome": "ROLLED_BACK", "failing_postcondition": 0},
    ],
    ids=["rejected", "changes requested", "rolled back at the last index", "rolled back at 0"],
)
def test_verdict_names_what_failed_for_each_failing_outcome(changes: dict[str, object]) -> None:
    verdict = _rebuild(VERDICT, **changes)

    assert isinstance(verdict, Verdict)
    assert verdict.outcome is VerdictOutcome(changes["outcome"])


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"outcome": "REJECTED"}, "failing_check is set exactly when"),
        ({"outcome": "CHANGES_REQUESTED"}, "failing_check is set exactly when"),
        ({"failing_check": "LINT"}, "failing_check is set exactly when"),
        ({"outcome": "ROLLED_BACK"}, "failing_postcondition is set exactly when"),
        ({"failing_postcondition": 0}, "failing_postcondition is set exactly when"),
        (
            {"outcome": "REJECTED", "failing_check": "LINT", "failing_postcondition": 0},
            "failing_postcondition is set exactly when",
        ),
        (
            {"outcome": "ROLLED_BACK", "failing_postcondition": 0, "failing_check": "LINT"},
            "failing_check is set exactly when",
        ),
        (
            {"outcome": "ROLLED_BACK", "failing_postcondition": MAX_POSTCONDITION_INDEX + 1},
            f"less than or equal to {MAX_POSTCONDITION_INDEX}",
        ),
        ({"outcome": "ROLLED_BACK", "failing_postcondition": -1}, "greater than or equal to 0"),
        ({"outcome": "REJECTED", "failing_check": "SPELLING"}, "failing_check"),
        ({"task_id": CELL_ID}, "task_"),
        ({"cell_id": TASK_ID}, "cell_"),
    ],
)
def test_verdict_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(VERDICT, **changes)


# ──────────────────────────────────────────────────────────────────────────────
# PostconditionResult
# ──────────────────────────────────────────────────────────────────────────────


def test_postcondition_result_serves_acceptance_checks_and_defaults_observed() -> None:
    acceptance = PostconditionResult.model_validate(
        {**POSTCONDITION_RESULT.model_dump(exclude={"observed"}), "proposal_id": None}
    )

    assert acceptance.proposal_id is None
    assert acceptance.observed == ""


def test_postcondition_result_reports_a_failed_assertion() -> None:
    failed = _rebuild(
        POSTCONDITION_RESULT,
        index=MAX_POSTCONDITION_INDEX,
        has_held=False,
        observed="1 failed in 0.3s",
        clearance="C2",
    )

    assert isinstance(failed, PostconditionResult)
    assert failed.has_held is False
    assert failed.clearance is HoneyClearance.C2


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"index": MAX_POSTCONDITION_INDEX + 1},
            f"less than or equal to {MAX_POSTCONDITION_INDEX}",
        ),
        ({"index": -1}, "greater than or equal to 0"),
        ({"observed": "x" * (MAX_OBSERVED_CHARS + 1)}, f"at most {MAX_OBSERVED_CHARS}"),
        ({"proposal_id": CELL_ID}, "msg_"),
        ({"task_id": CELL_ID}, "task_"),
        ({"cell_id": TASK_ID}, "cell_"),
        ({"kind": "EXISTS"}, "kind"),
    ],
)
def test_postcondition_result_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(POSTCONDITION_RESULT, **changes)


# ──────────────────────────────────────────────────────────────────────────────
# RollbackDone
# ──────────────────────────────────────────────────────────────────────────────


def test_rollback_done_accepts_a_snapshot_restore_and_an_incomplete_no_op() -> None:
    snapshot = _rebuild(ROLLBACK, method="SNAPSHOT", snapshot_id="zfs@pre-apply")
    no_op = _rebuild(ROLLBACK, method="NONE", is_complete=False, residual_paths=("/etc/hosts",))

    assert isinstance(snapshot, RollbackDone)
    assert snapshot.snapshot_id == "zfs@pre-apply"
    assert isinstance(no_op, RollbackDone)
    assert no_op.residual_paths == ("/etc/hosts",)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"method": "SNAPSHOT"}, "snapshot_id is set exactly when method is SNAPSHOT"),
        ({"snapshot_id": "zfs@pre-apply"}, "snapshot_id is set exactly when method is SNAPSHOT"),
        (
            {"method": "NONE", "snapshot_id": "zfs@pre-apply"},
            "snapshot_id is set exactly when method is SNAPSHOT",
        ),
        ({"residual_paths": ("/etc/hosts",)}, "residual_paths is empty exactly when is_complete"),
        ({"is_complete": False}, "residual_paths is empty exactly when is_complete"),
        (
            {"is_complete": False, "residual_paths": ("p",) * (MAX_RESIDUAL_PATHS + 1)},
            f"at most {MAX_RESIDUAL_PATHS}",
        ),
        (
            {"is_complete": False, "residual_paths": ("x" * (MAX_PATH_CHARS + 1),)},
            f"at most {MAX_PATH_CHARS}",
        ),
        (
            {"method": "SNAPSHOT", "snapshot_id": "x" * (MAX_SNAPSHOT_ID_CHARS + 1)},
            f"at most {MAX_SNAPSHOT_ID_CHARS}",
        ),
        ({"method": "UNDO"}, "method"),
        ({"task_id": CELL_ID}, "task_"),
        ({"cell_id": TASK_ID}, "cell_"),
    ],
)
def test_rollback_done_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(ROLLBACK, **changes)
