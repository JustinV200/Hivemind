"""Tests for the tool family's calling half (waggle.messages.tool.call): ToolInvoke, ToolResult.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins, for ToolInvoke and ToolResult,
    construction, the rejection of an extra field, at least one bound, and every validator
    spec section 8.8 names: the JSON-object check on arguments_json, the any-JSON-value check
    on output_json, and the error-code rule on the outcome. The family's EXAMPLES tuple, the
    round trip and the enum pins live in test_authoring.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.tool.call and waggle.messages.tool.json_text for the modules under test.
    - test_authoring.py for EXAMPLES and the family's authoring half.
    - docs/waggle/spec.md section 8.8 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import WaggleMessage
from waggle.messages.labels import HoneyClearance
from waggle.messages.tool.call import (
    MAX_ARGUMENTS_CHARS,
    MAX_ERROR_CODE_CHARS,
    MAX_OUTPUT_CHARS,
    ToolInvoke,
    ToolInvokePurpose,
    ToolOutcome,
    ToolResult,
)

CLOCK = FakeClock()
DEEP_NESTING = "[" * 5_000  # Deeper than the JSON decoder's recursion limit, well under every cap.
INVOKE = ToolInvoke(
    tool_id=new_id(IdKind.TOOL, CLOCK),
    version="1.0.0",
    cell_id=new_id(IdKind.CELL, CLOCK),
    task_id=new_id(IdKind.TASK, CLOCK),
    worker_id=new_id(IdKind.WORKER, CLOCK),
    purpose=ToolInvokePurpose.WORK,
    arguments_json='{"path": "scratch/data.csv"}',
    timeout_s=30.0,
    reason="Profile the input before planning the cleanup.",
)
RESULT = ToolResult(
    tool_id=new_id(IdKind.TOOL, CLOCK),
    version="1.0.0",
    outcome=ToolOutcome.SUCCEEDED,
    output_json='{"columns": 12, "rows": 4096}',
    clearance=HoneyClearance.C1,
    error_code=None,
    duration_s=0.42,
    reason="Profiled 12 columns.",
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


# ──────────────────────────────────────────────────────────────────────────────
# ToolInvoke
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"arguments_json": "[1, 2]"}, "JSON object"),
        ({"arguments_json": "42"}, "JSON object"),
        ({"arguments_json": "{"}, "not valid JSON"),
        ({"arguments_json": ""}, "not valid JSON"),
        ({"arguments_json": DEEP_NESTING}, "not valid JSON"),
        (
            {"arguments_json": '{"a": "' + "x" * MAX_ARGUMENTS_CHARS + '"}'},
            f"at most {MAX_ARGUMENTS_CHARS}",
        ),
        ({"timeout_s": 0.0}, "greater than 0"),
        ({"timeout_s": -1.0}, "greater than 0"),
        ({"tool_id": new_id(IdKind.CELL, CLOCK)}, "tool_"),
        ({"cell_id": new_id(IdKind.TOOL, CLOCK)}, "cell_"),
        ({"task_id": new_id(IdKind.TOOL, CLOCK)}, "task_"),
        ({"worker_id": new_id(IdKind.WARDEN, CLOCK)}, "worker_"),
        ({"hop_count": 1}, "extra"),
    ],
)
def test_tool_invoke_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(INVOKE, **changes)


def test_tool_invoke_accepts_a_quarantine_run_driven_by_the_queen() -> None:
    run = _rebuild(INVOKE, purpose="QUARANTINE", task_id=None, worker_id=None, arguments_json="{}")

    assert isinstance(run, ToolInvoke)
    assert run.purpose is ToolInvokePurpose.QUARANTINE
    assert run.arguments_json == "{}"


def test_tool_invoke_keeps_its_arguments_as_text() -> None:
    nested = _rebuild(INVOKE, arguments_json='{"a": {"b": [1, 2, {"c": null}]}}')

    assert isinstance(nested, ToolInvoke)
    assert isinstance(nested.arguments_json, str)
    assert nested.model_dump(mode="json")["arguments_json"] == nested.arguments_json


# ──────────────────────────────────────────────────────────────────────────────
# ToolResult
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["FAILED", "DENIED", "TIMED_OUT"])
def test_tool_result_requires_an_error_code_on_every_non_success(outcome: str) -> None:
    failed = _rebuild(RESULT, outcome=outcome, error_code="hive.tool.failed", output_json="")

    assert isinstance(failed, ToolResult)
    assert failed.error_code == "hive.tool.failed"
    with pytest.raises(ValidationError, match="exactly when outcome is not SUCCEEDED"):
        _rebuild(RESULT, outcome=outcome, error_code=None)


def test_tool_result_refuses_an_error_code_on_success() -> None:
    with pytest.raises(ValidationError, match="exactly when outcome is not SUCCEEDED"):
        _rebuild(RESULT, error_code="hive.tool.failed")


@pytest.mark.parametrize("output", ["", "42", '"text"', "[1, 2]", "null", "true", "{}"])
def test_tool_result_accepts_any_json_value_or_no_output(output: str) -> None:
    result = _rebuild(RESULT, output_json=output)

    assert isinstance(result, ToolResult)
    assert result.output_json == output
    assert result.is_truncated is False


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"output_json": "{bad"}, "not valid JSON"),
        ({"output_json": "text"}, "not valid JSON"),
        ({"output_json": DEEP_NESTING}, "not valid JSON"),
        ({"output_json": '"' + "x" * MAX_OUTPUT_CHARS + '"'}, f"at most {MAX_OUTPUT_CHARS}"),
        (
            {"outcome": "FAILED", "error_code": "x" * (MAX_ERROR_CODE_CHARS + 1)},
            f"at most {MAX_ERROR_CODE_CHARS}",
        ),
        ({"duration_s": -0.1}, "greater than or equal to 0"),
        ({"clearance": "public"}, "clearance"),
        ({"tool_id": new_id(IdKind.TASK, CLOCK)}, "tool_"),
        ({"hop_count": 1}, "extra"),
    ],
)
def test_tool_result_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(RESULT, **changes)


def test_tool_result_marks_a_truncated_output_explicitly() -> None:
    truncated = _rebuild(RESULT, output_json='"partial"', is_truncated=True)

    assert truncated.model_dump(mode="json")["is_truncated"] is True
    assert RESULT.model_dump(mode="json")["is_truncated"] is False
