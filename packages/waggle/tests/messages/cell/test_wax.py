"""Tests for waggle.messages.cell.wax: a Cell Wax note proposed, written and cleared.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). For CellWaxProposed, CellWaxWritten and
    CellWaxCleared it pins construction for every origin the spec allows, the JSON round trip,
    the rejection of an extra field, every bound including the wax_ id pattern, and the
    proposer-matches-origin validator in both directions on both messages that carry it. The
    family's EXAMPLES and the wax enums' members live in test_status.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.cell.wax for the module under test.
    - docs/waggle/spec.md section 8.5 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.wax import (
    MAX_WAX_TEXT_CHARS,
    CellWaxCleared,
    CellWaxProposed,
    CellWaxWritten,
    WaxClearCause,
    WaxDecision,
    WaxOrigin,
    WaxSeverity,
)
from waggle.messages.labels import HoneyClearance

CLOCK = FakeClock()
NOW = CLOCK.now()
LATER = NOW + timedelta(days=7)
CELL_ID = new_id(IdKind.CELL, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
WORKER_ID = new_id(IdKind.WORKER, CLOCK)
TASK_ID = new_id(IdKind.TASK, CLOCK)
WAX_ID = "wax_01ARZ3NDEKTSV4RRFFQ69G5FAV"  # A wax_ prefix on a well-formed Crockford ULID.
# (origin, proposer) pairs the validator accepts: a bee names itself, the human names nobody.
ORIGINS_AND_PROPOSERS = [
    (WaxOrigin.BEE, WORKER_ID),
    (WaxOrigin.PATROL, WARDEN_ID),
    (WaxOrigin.HUMAN, None),
]
# wax_ ids that fail the pattern: wrong prefix, lowercase, too short, an excluded letter (I).
BAD_WAX_IDS = [
    "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "wax_01arz3ndektsv4rrffq69g5fav",
    "wax_01ARZ3NDEKTSV4RRFFQ69G5FA",
    "wax_01ARZ3NDEKTSV4RRFFQ69G5FAI",
]


def _round_trips(message: WaggleMessage) -> bool:
    """Whether ``message`` survives model_dump(mode="json") and model_validate unchanged."""
    return type(message).model_validate(message.model_dump(mode="json")) == message


def _proposed(**overrides: object) -> CellWaxProposed:
    """A Worker's CAUTION about the Cell it works on, then ``overrides``."""
    fields: dict[str, object] = {
        "cell_id": CELL_ID,
        "severity": "CAUTION",
        "text": "The package manager prompts for a password every run.",
        "reason": "Three installs stalled on it.",
        "clearance": "C2",
        "expires_at": LATER,
        "origin": "BEE",
        "proposer": WORKER_ID,
        "task_id": TASK_ID,
    }
    return CellWaxProposed.model_validate({**fields, **overrides})


def _written(**overrides: object) -> CellWaxWritten:
    """The same note as the Queen wrote it by rule, then ``overrides``."""
    fields: dict[str, object] = {
        "wax_id": WAX_ID,
        "cell_id": CELL_ID,
        "severity": "CAUTION",
        "text": "The package manager prompts for a password every run.",
        "reason": "Confirmed by the Patrol.",
        "clearance": "C2",
        "expires_at": LATER,
        "origin": "BEE",
        "proposer": WORKER_ID,
        "decided_by": "AUTOPILOT",
    }
    return CellWaxWritten.model_validate({**fields, **overrides})


def _cleared(**overrides: object) -> CellWaxCleared:
    """The note cleared by the Queen, then ``overrides``."""
    fields: dict[str, object] = {
        "wax_id": WAX_ID,
        "cell_id": CELL_ID,
        "cause": "CLEARED",
        "reason": "The prompt was fixed by the operator.",
    }
    return CellWaxCleared.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# CellWaxProposed and CellWaxWritten: the shared proposer rule
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("builder", [_proposed, _written], ids=["proposed", "written"])
@pytest.mark.parametrize(
    ("origin", "proposer"), ORIGINS_AND_PROPOSERS, ids=lambda value: getattr(value, "name", "")
)
def test_wax_proposer_is_named_exactly_when_a_bee_noticed_it(
    builder: Callable[..., CellWaxProposed | CellWaxWritten],
    origin: WaxOrigin,
    proposer: str | None,
) -> None:
    message = builder(origin=origin.value, proposer=proposer)

    assert message.origin is origin
    assert message.proposer == proposer
    assert _round_trips(message)


@pytest.mark.parametrize("builder", [_proposed, _written], ids=["proposed", "written"])
@pytest.mark.parametrize(
    ("origin", "proposer", "reason"),
    [
        ("HUMAN", WORKER_ID, "proposer must be None exactly when origin is HUMAN"),
        ("BEE", None, "proposer must be None exactly when origin is HUMAN"),
        ("PATROL", None, "proposer must be None exactly when origin is HUMAN"),
        ("BEE", TASK_ID, "accepted prefixes worker_, warden_"),
        ("BEE", new_id(IdKind.HIVE, CLOCK), "accepted prefixes worker_, warden_"),
    ],
)
def test_wax_proposer_rule_rejections(
    builder: Callable[..., CellWaxProposed | CellWaxWritten],
    origin: str,
    proposer: str | None,
    reason: str,
) -> None:
    with pytest.raises(ValidationError, match=reason):
        builder(origin=origin, proposer=proposer)


@pytest.mark.parametrize("builder", [_proposed, _written], ids=["proposed", "written"])
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"text": ""}, "at least 1"),
        ({"text": "x" * (MAX_WAX_TEXT_CHARS + 1)}, f"at most {MAX_WAX_TEXT_CHARS}"),
        ({"expires_at": datetime(2020, 1, 1)}, "timezone-aware"),  # naive on purpose
        ({"severity": "FATAL"}, "severity"),
        ({"clearance": "C3"}, "clearance"),
        ({"cell_id": WORKER_ID}, "cell_"),
        ({"author": "me"}, "extra"),
    ],
)
def test_wax_note_bounds(
    builder: Callable[..., CellWaxProposed | CellWaxWritten],
    changes: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(ValidationError, match=reason):
        builder(**changes)


@pytest.mark.parametrize("builder", [_proposed, _written], ids=["proposed", "written"])
def test_wax_note_accepts_standing_wax_at_every_severity_and_clearance(
    builder: Callable[..., CellWaxProposed | CellWaxWritten],
) -> None:
    for severity in WaxSeverity:
        for clearance in HoneyClearance:
            note = builder(severity=severity.value, clearance=clearance.value, expires_at=None)
            assert note.severity is severity
            assert note.clearance is clearance
            assert note.expires_at is None


# ──────────────────────────────────────────────────────────────────────────────
# CellWaxProposed
# ──────────────────────────────────────────────────────────────────────────────


def test_wax_proposed_task_is_optional_and_typed() -> None:
    assert _proposed(task_id=None).task_id is None
    with pytest.raises(ValidationError, match="task_"):
        _proposed(task_id=CELL_ID)


# ──────────────────────────────────────────────────────────────────────────────
# CellWaxWritten, CellWaxCleared
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("builder", [_written, _cleared], ids=["written", "cleared"])
@pytest.mark.parametrize("wax_id", BAD_WAX_IDS)
def test_wax_id_must_match_the_wax_pattern(
    builder: Callable[..., CellWaxWritten | CellWaxCleared], wax_id: str
) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        builder(wax_id=wax_id)


def test_wax_written_records_how_it_was_decided() -> None:
    for decision in WaxDecision:
        assert _written(decided_by=decision.value).decided_by is decision
    with pytest.raises(ValidationError, match="decided_by"):
        _written(decided_by="HUMAN")


def test_wax_cleared_constructs_round_trips_and_carries_every_cause() -> None:
    for cause in WaxClearCause:
        cleared = _cleared(cause=cause.value)
        assert cleared.cause is cause
        assert _round_trips(cleared)
    with pytest.raises(ValidationError, match="extra"):
        _cleared(severity="NOTE")
