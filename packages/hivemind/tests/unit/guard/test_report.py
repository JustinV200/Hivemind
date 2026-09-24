"""Tests for hivemind.guard.report: a report cites evidence, names its target, and stays bounded.

Fits into the Hive:
    Mirrors src/hivemind/guard/report.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.report for the contract under test.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hivemind.guard import (
    REQUEST_ACTIONS,
    GuardAction,
    GuardConfidence,
    GuardReport,
    new_guard_report_id,
)
from hivemind.guard.report import GUARD_REPORT_ID_PATTERN, MAX_CITED_EVENTS, MAX_SUMMARY_CHARS
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_grant_id, new_task_id, new_worker_id

_CLOCK = FakeClock(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))


def _fields(**overrides: object) -> dict[str, object]:
    """A valid isolate request, with `overrides` applied."""
    fields: dict[str, object] = {
        "id": new_guard_report_id(_CLOCK),
        "rule": "injection_then_denial",
        "event_ids": (new_event_id(_CLOCK), new_event_id(_CLOCK)),
        "cell_id": new_cell_id(_CLOCK),
        "bee_ids": (new_worker_id(_CLOCK),),
        "task_ids": (new_task_id(_CLOCK),),
        "grant_ids": (new_grant_id(_CLOCK),),
        "recommended": GuardAction.ISOLATE_CELL,
        "confidence": GuardConfidence.HIGH,
        "filed_at": _CLOCK.now(),
        "summary": "1 injection-suspected event, then 1 denial in the same episode.",
    }
    fields.update(overrides)
    return fields


def test_a_well_formed_request_round_trips_through_json() -> None:
    report = GuardReport.model_validate(_fields())
    assert GuardReport.model_validate_json(report.model_dump_json()) == report
    assert report.is_request


@pytest.mark.parametrize("action", [GuardAction.ISOLATE_CELL, GuardAction.STING_CUT])
def test_a_cell_level_request_must_name_its_cell(action: GuardAction) -> None:
    with pytest.raises(ValidationError, match="must name its Cell"):
        GuardReport.model_validate(_fields(recommended=action, cell_id=None))


def test_a_quarantine_request_must_name_a_bee_or_a_task() -> None:
    fields = _fields(recommended=GuardAction.QUARANTINE_BEE, bee_ids=(), task_ids=())
    with pytest.raises(ValidationError, match="must name a bee or a task"):
        GuardReport.model_validate(fields)
    # Either one is enough: a task names the bee running it.
    assert GuardReport.model_validate({**fields, "task_ids": (new_task_id(_CLOCK),)}).is_request


@pytest.mark.parametrize(
    "action", [GuardAction.RAISE_AUDIT_RATE, GuardAction.REDUCE_ENTRANCE, GuardAction.OBSERVE]
)
def test_a_hive_wide_or_record_only_report_needs_no_target_and_is_no_request(
    action: GuardAction,
) -> None:
    report = GuardReport.model_validate(
        _fields(recommended=action, cell_id=None, bee_ids=(), task_ids=(), grant_ids=())
    )
    assert not report.is_request
    assert action not in REQUEST_ACTIONS


def test_a_report_cites_at_least_one_event_and_never_an_unbounded_window() -> None:
    with pytest.raises(ValidationError):
        GuardReport.model_validate(_fields(event_ids=()))
    too_many = tuple(new_event_id(_CLOCK) for _ in range(MAX_CITED_EVENTS + 1))
    with pytest.raises(ValidationError):
        GuardReport.model_validate(_fields(event_ids=too_many))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_ids", ("task_01J00000000000000000000000",)),  # Not an event id.
        ("cell_id", "warden_01J00000000000000000000000"),  # Not a Cell id.
        ("bee_ids", ("cell_01J00000000000000000000000",)),  # Neither a Worker nor a Warden.
        ("rule", "Injection Then Denial"),  # A rule key is a data key, never prose.
        ("summary", "x" * (MAX_SUMMARY_CHARS + 1)),
        ("summary", ""),
        ("id", "report_01J00000000000000000000000"),
    ],
)
def test_malformed_fields_are_refused(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        GuardReport.model_validate(_fields(**{field: value}))


def test_confidence_orders_lowest_first() -> None:
    order = list(GuardConfidence)
    assert [tier.rank for tier in order] == list(range(len(order)))
    assert GuardConfidence.CRITICAL.at_least(GuardConfidence.HIGH)
    assert GuardConfidence.HIGH.at_least(GuardConfidence.HIGH)
    assert not GuardConfidence.MEDIUM.at_least(GuardConfidence.HIGH)


def test_report_ids_match_their_pattern_and_sort_by_time() -> None:
    clock = FakeClock(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))
    first = new_guard_report_id(clock)
    clock.advance(1.0)
    second = new_guard_report_id(clock)
    assert re.fullmatch(GUARD_REPORT_ID_PATTERN, first)
    assert first < second
