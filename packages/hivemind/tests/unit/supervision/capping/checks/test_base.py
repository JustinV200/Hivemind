"""Unit tests for hivemind.supervision.capping.checks.base: CheckResultRecord round trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.supervision.capping.checks.base import CheckResultRecord
from waggle.messages.capping import CheckKind, CheckOutcome


def test_check_result_record_round_trips_through_json() -> None:
    record = CheckResultRecord(kind=CheckKind.SCHEMA, outcome=CheckOutcome.PASSED, reason="ok")

    restored = CheckResultRecord.model_validate_json(record.model_dump_json())

    assert restored == record


def test_check_result_record_is_frozen_and_forbids_extras() -> None:
    record = CheckResultRecord(kind=CheckKind.SCHEMA, outcome=CheckOutcome.PASSED, reason="ok")

    with pytest.raises(ValidationError, match="frozen"):
        record.outcome = CheckOutcome.FAILED  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError):
        CheckResultRecord.model_validate({**record.model_dump(), "extra": "nope"})
