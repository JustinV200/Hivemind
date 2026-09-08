"""Tests for the capping family's gate entry (waggle.messages.capping): the proposal, the checks.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    capping class (the verdict half included, so the registry step can load the whole family
    by this one file path); and for ProposalSubmitted and CheckResult pins construction, the
    JSON round trip, the rejection of an extra field, at least one bound, and every validator
    spec section 8.9 names, in both directions.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the five capping classes.

See Also:
    - waggle.messages.capping for the module under test.
    - test_capping_action.py and test_capping_verdict.py for the family's other modules.
    - docs/waggle/spec.md section 8.9 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from enum import Enum

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_PATH_CHARS, MAX_REASON_CHARS, WaggleMessage
from waggle.messages.capping import (
    MAX_DETAIL_CHARS,
    MAX_POSTCONDITIONS,
    MAX_POSTCONDITIONS_CHARS,
    CheckKind,
    CheckOutcome,
    CheckResult,
    ProposalSubmitted,
    RiskTier,
)
from waggle.messages.capping_action import ActionKind, ProposedAction
from waggle.messages.capping_verdict import (
    PostconditionResult,
    RollbackDone,
    RollbackMethod,
    Verdict,
    VerdictOutcome,
)
from waggle.messages.labels import (
    AccuracyBar,
    HoneyClearance,
    Postcondition,
    PostconditionKind,
    Tempo,
)

CLOCK = FakeClock()
CAPPING_CLASSES: tuple[type[WaggleMessage], ...] = (
    ProposalSubmitted,
    CheckResult,
    Verdict,
    PostconditionResult,
    RollbackDone,
)
ACTION = ProposedAction(
    kind=ActionKind.DIFF,
    summary="Add the enrol route to the Landing Board.",
    diff="--- a/enrol.py\n+++ b/enrol.py\n@@ -1 +1,2 @@\n line\n+route\n",
    diff_sha256=None,
    command=(),
    cwd=None,
    paths=("src/hivemind/entrance/enrol.py",),
    steps=(),
)
POSTCONDITION = Postcondition(
    kind=PostconditionKind.TEST_PASSES,
    subject="tests/entrance/test_enrol.py",
    argv=("pytest", "-q", "tests/entrance/test_enrol.py"),
    expected=None,
)
TEMPO = Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL)
PROPOSAL_ID = new_id(IdKind.MESSAGE, CLOCK)
TASK_ID = new_id(IdKind.TASK, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)

# One valid instance of every capping class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    ProposalSubmitted(
        task_id=TASK_ID,
        cell_id=CELL_ID,
        proposer=new_id(IdKind.WORKER, CLOCK),
        risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE,
        action=ACTION,
        postconditions=(POSTCONDITION,),
        tempo=TEMPO,
        clearance=HoneyClearance.C1,
        reason="The route is the next step of the task plan.",
    ),
    CheckResult(
        proposal_id=PROPOSAL_ID,
        task_id=TASK_ID,
        cell_id=CELL_ID,
        check=CheckKind.LINT,
        outcome=CheckOutcome.PASSED,
        reason="ruff reported nothing.",
        clearance=HoneyClearance.C1,
        duration_s=0.8,
    ),
    Verdict(
        proposal_id=PROPOSAL_ID,
        task_id=TASK_ID,
        cell_id=CELL_ID,
        outcome=VerdictOutcome.VERIFIED,
        reason="Applied; the test passed.",
        failing_check=None,
        failing_postcondition=None,
    ),
    PostconditionResult(
        proposal_id=PROPOSAL_ID,
        task_id=TASK_ID,
        cell_id=CELL_ID,
        index=0,
        kind=PostconditionKind.TEST_PASSES,
        has_held=True,
        observed="1 passed in 0.3s",
        clearance=HoneyClearance.C1,
        reason="The test passed on the first run.",
    ),
    RollbackDone(
        proposal_id=PROPOSAL_ID,
        task_id=TASK_ID,
        cell_id=CELL_ID,
        method=RollbackMethod.REVERSE_DIFF,
        snapshot_id=None,
        is_complete=True,
        residual_paths=(),
        reason="Postcondition 0 did not hold; the diff was reversed cleanly.",
    ),
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_capping_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == CAPPING_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_capping_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.reason = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_capping_message_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_reason_is_bounded_by_the_shared_limit(example: WaggleMessage) -> None:
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


@pytest.mark.parametrize(
    ("enum_type", "names"),
    [
        (
            RiskTier,
            [
                "READ_ONLY",
                "SCRATCH_WRITE",
                "OUTSIDE_SCRATCH_WRITE",
                "NETWORK_EGRESS",
                "SPEND",
                "DEVICE_COMMAND",
                "IRREVERSIBLE",
            ],
        ),
        (
            CheckKind,
            ["SCHEMA", "LINT", "TYPES", "ALLOWLIST", "SIZE_CAP", "SANDBOX_TESTS", "JUDGE", "HUMAN"],
        ),
        (CheckOutcome, ["PASSED", "FAILED", "CHANGES_REQUESTED"]),
    ],
)
def test_capping_enum_members_and_values_match_the_spec(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert all(member.value == member.name for member in enum_type)


@pytest.mark.parametrize("message_type", [CheckResult, Verdict, RollbackDone])
def test_proposal_id_must_be_a_message_id(message_type: type[WaggleMessage]) -> None:
    with pytest.raises(ValidationError, match="msg_"):
        _rebuild(_example(message_type), proposal_id=TASK_ID)


# ──────────────────────────────────────────────────────────────────────────────
# ProposalSubmitted
# ──────────────────────────────────────────────────────────────────────────────


def test_proposal_may_state_no_postcondition_only_when_read_only() -> None:
    read = _rebuild(_example(ProposalSubmitted), risk_tier="READ_ONLY", postconditions=())

    assert isinstance(read, ProposalSubmitted)
    assert read.postconditions == ()
    with pytest.raises(ValidationError, match="only READ_ONLY may state none"):
        _rebuild(_example(ProposalSubmitted), risk_tier="SCRATCH_WRITE", postconditions=())


def test_proposal_spend_estimate_is_non_zero_exactly_for_the_spend_tier() -> None:
    example = _example(ProposalSubmitted)
    assert isinstance(example, ProposalSubmitted)
    assert example.spend_estimate == 0.0

    spend = _rebuild(example, risk_tier="SPEND", spend_estimate=1.5)

    assert isinstance(spend, ProposalSubmitted)
    assert spend.spend_estimate == 1.5
    with pytest.raises(ValidationError, match="non-zero exactly for the SPEND tier"):
        _rebuild(example, risk_tier="SPEND")
    with pytest.raises(ValidationError, match="non-zero exactly for the SPEND tier"):
        _rebuild(example, spend_estimate=1.5)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _rebuild(example, risk_tier="SPEND", spend_estimate=-1.0)


def test_proposal_bounds_its_postconditions_in_count_and_in_text() -> None:
    example = _example(ProposalSubmitted)
    # The widest single postcondition: a subject at the path cap and nothing else, so the text
    # cap is reached with a whole number of them and one more tips it over.
    widest = Postcondition(
        kind=PostconditionKind.FILE_EXISTS, subject="x" * MAX_PATH_CHARS, argv=(), expected=None
    )
    fitting = MAX_POSTCONDITIONS_CHARS // MAX_PATH_CHARS

    assert _rebuild(example, postconditions=(widest,) * fitting)
    with pytest.raises(ValidationError, match=f"at most {MAX_POSTCONDITIONS_CHARS} characters"):
        _rebuild(example, postconditions=(widest,) * (fitting + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_POSTCONDITIONS}"):
        _rebuild(example, postconditions=(POSTCONDITION,) * (MAX_POSTCONDITIONS + 1))


def test_proposal_counts_argv_and_expected_towards_the_text_cap() -> None:
    # A command postcondition's argv and a rubric's expected text count as well as the subject.
    wide_argv = Postcondition(
        kind=PostconditionKind.COMMAND_EXITS_ZERO,
        subject="x",
        argv=("y" * 1_024,) * 32,
        expected=None,
    )
    too_many = MAX_POSTCONDITIONS_CHARS // (1 + 32 * 1_024) + 1

    with pytest.raises(ValidationError, match=f"at most {MAX_POSTCONDITIONS_CHARS} characters"):
        _rebuild(_example(ProposalSubmitted), postconditions=(wide_argv,) * too_many)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"proposer": new_id(IdKind.WARDEN, CLOCK)}, "worker_"),
        ({"task_id": CELL_ID}, "task_"),
        ({"cell_id": TASK_ID}, "cell_"),
        ({"clearance": "public"}, "clearance"),
        ({"risk_tier": "LOW"}, "risk_tier"),
    ],
)
def test_proposal_ids_and_labels(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(ProposalSubmitted), **changes)


# ──────────────────────────────────────────────────────────────────────────────
# CheckResult
# ──────────────────────────────────────────────────────────────────────────────


def test_check_result_detail_defaults_empty_and_is_bounded() -> None:
    example = _example(CheckResult)
    assert isinstance(example, CheckResult)
    assert example.detail == ""
    assert _rebuild(example, detail="x" * MAX_DETAIL_CHARS, outcome="FAILED")
    with pytest.raises(ValidationError, match=f"at most {MAX_DETAIL_CHARS}"):
        _rebuild(example, detail="x" * (MAX_DETAIL_CHARS + 1))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"duration_s": -0.1}, "greater than or equal to 0"),
        ({"task_id": CELL_ID}, "task_"),
        ({"cell_id": TASK_ID}, "cell_"),
        ({"check": "SPELLING"}, "check"),
        ({"outcome": "SKIPPED"}, "outcome"),
    ],
)
def test_check_result_bounds_and_ids(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(CheckResult), **changes)


def test_check_result_carries_a_human_or_judge_changes_request() -> None:
    requested = _rebuild(
        _example(CheckResult), check="HUMAN", outcome="CHANGES_REQUESTED", detail="Rename it."
    )

    assert isinstance(requested, CheckResult)
    assert requested.check is CheckKind.HUMAN
    assert requested.outcome is CheckOutcome.CHANGES_REQUESTED
