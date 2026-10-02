"""Pin protocol 1.14's TaskResult.cause: a typed cause an attempt ended as it did (ADR-0060).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.messages.task.reports against spec
    section 8.2: the field's default, its round trip and its closed enum.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 8.2 for TaskResult and section 4 for minor 1.14.
    - docs/adr/0060-commands-run-only-where-they-are-contained-or-fully-granted.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.labels import HoneyClearance
from waggle.messages.task import ResultCause, TaskOutcome, TaskResult


def _claim(**changes: object) -> dict[str, object]:
    """A Worker's claim as a wire dict, with `changes` applied."""
    fields: dict[str, object] = {
        "task_id": new_id(IdKind.TASK, FakeClock()),
        "attempt": 1,
        "outcome": TaskOutcome.CLAIMED.value,
        "summary": "Tried.",
        "clearance": HoneyClearance.C1.value,
        "artifacts": [],
        "checked_by": None,
        "handoff": None,
        "spend": 0.0,
        "reason": "The role completed its work.",
    }
    return {**fields, **changes}


def test_cause_defaults_to_none_so_an_older_peers_result_still_validates() -> None:
    assert TaskResult.model_validate(_claim()).cause is None


def test_cause_round_trips_through_the_wire_form() -> None:
    result = TaskResult.model_validate(_claim(cause="CELL_RUNS_NO_COMMANDS"))

    dumped = result.model_dump(mode="json")

    assert result.cause is ResultCause.CELL_RUNS_NO_COMMANDS
    assert dumped["cause"] == "CELL_RUNS_NO_COMMANDS"
    assert TaskResult.model_validate(dumped) == result


def test_an_unknown_cause_is_refused() -> None:
    with pytest.raises(ValidationError):
        TaskResult.model_validate(_claim(cause="SOMETHING_ELSE"))
