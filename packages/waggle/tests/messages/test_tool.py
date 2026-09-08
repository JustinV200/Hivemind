"""Tests for the tool family's authoring half (waggle.messages.tool): ToolRequest, ToolPromoted.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    tool class (the calling half included, so the registry step can load the whole family by
    this one file path); and for ToolRequest and ToolPromoted pins construction, the JSON round
    trip, the rejection of an extra field, at least one bound, and every validator spec section
    8.8 names, the JSON-object check on input_schema_json included.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the four tool classes.

See Also:
    - waggle.messages.tool for the module under test.
    - test_tool_call.py for ToolInvoke and ToolResult, the family's other half.
    - docs/waggle/spec.md section 8.8 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, WaggleMessage
from waggle.messages.labels import HoneyClearance
from waggle.messages.reports import PlatformReport
from waggle.messages.tool import (
    MAX_CAPABILITY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_INPUT_SCHEMA_CHARS,
    MAX_REQUIRED_CAPABILITIES,
    MAX_VERSION_CHARS,
    QuarantinePath,
    ToolPromoted,
    ToolRequest,
    ToolScope,
)
from waggle.messages.tool_call import ToolInvoke, ToolInvokePurpose, ToolOutcome, ToolResult

CLOCK = FakeClock()
NOW = CLOCK.now()
SHA256 = "ab" * 32  # 32 digest bytes as 64 lowercase hex characters.
DEEP_NESTING = "[" * 5_000  # Deeper than the JSON decoder's recursion limit, well under every cap.
TOOL_CLASSES: tuple[type[WaggleMessage], ...] = (ToolRequest, ToolPromoted, ToolInvoke, ToolResult)
# Built from a dict rather than keywords: the `shell` field name trips bandit's S604 (a
# shell=True check) on a keyword call, and a noqa would outlive the false positive.
PLATFORM = PlatformReport.model_validate(
    {
        "os": "LINUX",
        "distribution": "Ubuntu 24.04 LTS",
        "architecture": "x86_64",
        "package_manager": "apt",
        "shell": "/bin/bash",
        "python_version": "3.12.4",
    }
)

# One valid instance of every tool class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    ToolRequest(
        tool_id=new_id(IdKind.TOOL, CLOCK),
        warden_id=new_id(IdKind.WARDEN, CLOCK),
        worker_id=new_id(IdKind.WORKER, CLOCK),
        task_id=new_id(IdKind.TASK, CLOCK),
        cell_id=new_id(IdKind.CELL, CLOCK),
        scope=ToolScope.CELL,
        name="csv_profile",
        description="Summarise the columns and value ranges of a CSV file.",
        required_capabilities=("fs:read:scratch",),
        platform=PLATFORM,
        reason="The task's data arrives as CSV and no promoted tool reads it.",
    ),
    ToolPromoted(
        tool_id=new_id(IdKind.TOOL, CLOCK),
        name="csv_profile",
        version="1.0.0",
        scope=ToolScope.HIVE,
        cell_id=None,
        description="Summarise the columns and value ranges of a CSV file.",
        input_schema_json='{"type": "object", "properties": {"path": {"type": "string"}}}',
        required_capabilities=("fs:read:scratch",),
        supported_os=("LINUX", "MACOS"),
        quarantine_path=QuarantinePath.SANDBOX_CELL,
        quarantined_on=new_id(IdKind.CELL, CLOCK),
        quarantine_passed_at=NOW,
        package_sha256=SHA256,
        package_size_bytes=4_096,
        promoted_by_warden=None,
        supersedes_version=None,
        reason="Quarantine passed on the first run; the Queen approved the review.",
    ),
    ToolInvoke(
        tool_id=new_id(IdKind.TOOL, CLOCK),
        version="1.0.0",
        cell_id=new_id(IdKind.CELL, CLOCK),
        task_id=new_id(IdKind.TASK, CLOCK),
        worker_id=new_id(IdKind.WORKER, CLOCK),
        purpose=ToolInvokePurpose.WORK,
        arguments_json='{"path": "scratch/data.csv"}',
        timeout_s=30.0,
        reason="Profile the input before planning the cleanup.",
    ),
    ToolResult(
        tool_id=new_id(IdKind.TOOL, CLOCK),
        version="1.0.0",
        outcome=ToolOutcome.SUCCEEDED,
        output_json='{"columns": 12, "rows": 4096}',
        clearance=HoneyClearance.C1,
        error_code=None,
        duration_s=0.42,
        reason="Profiled 12 columns.",
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


def test_examples_hold_exactly_one_instance_of_every_tool_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == TOOL_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_tool_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.reason = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_tool_message_rejects_an_extra_field(example: WaggleMessage) -> None:
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
        (ToolScope, ["HIVE", "CELL"]),
        (QuarantinePath, ["SANDBOX_CELL", "REAL_CELL"]),
        (ToolInvokePurpose, ["WORK", "QUARANTINE"]),
        (ToolOutcome, ["SUCCEEDED", "FAILED", "DENIED", "TIMED_OUT"]),
    ],
)
def test_tool_enum_members_and_values_match_the_spec(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert all(member.value == member.name for member in enum_type)


@pytest.mark.parametrize("message_type", [ToolPromoted, ToolInvoke, ToolResult])
@pytest.mark.parametrize("version", ["1.2", "v1.2.3", "1.2.3-beta", "1.2.3.4", "", "a.b.c"])
def test_version_outside_the_semantic_pattern_is_rejected(
    message_type: type[WaggleMessage], version: str
) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        _rebuild(_example(message_type), version=version)


def test_version_is_bounded_by_its_length_as_well_as_its_pattern() -> None:
    longest = "1" * (MAX_VERSION_CHARS - 4) + ".0.0"
    assert _rebuild(_example(ToolPromoted), version=longest)
    with pytest.raises(ValidationError, match=f"at most {MAX_VERSION_CHARS}"):
        _rebuild(_example(ToolPromoted), version="1" + longest)


# ──────────────────────────────────────────────────────────────────────────────
# ToolRequest
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["a", "csv_profile", "x9", "a" + "b" * 63])
def test_tool_request_accepts_a_snake_case_name(name: str) -> None:
    assert _rebuild(_example(ToolRequest), name=name)


@pytest.mark.parametrize("name", ["", "A", "1a", "a-b", "a.b", "a" * 65, "csv profile"])
def test_tool_request_rejects_a_name_outside_the_pattern(name: str) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        _rebuild(_example(ToolRequest), name=name)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"description": ""}, "at least 1"),
        ({"description": "x" * (MAX_DESCRIPTION_CHARS + 1)}, f"at most {MAX_DESCRIPTION_CHARS}"),
        (
            {"required_capabilities": ("c",) * (MAX_REQUIRED_CAPABILITIES + 1)},
            f"at most {MAX_REQUIRED_CAPABILITIES}",
        ),
        (
            {"required_capabilities": ("c" * (MAX_CAPABILITY_CHARS + 1),)},
            f"at most {MAX_CAPABILITY_CHARS}",
        ),
        ({"tool_id": new_id(IdKind.TASK, CLOCK)}, "tool_"),
        ({"warden_id": new_id(IdKind.WORKER, CLOCK)}, "warden_"),
        ({"worker_id": new_id(IdKind.WARDEN, CLOCK)}, "worker_"),
        ({"task_id": new_id(IdKind.CELL, CLOCK)}, "task_"),
        ({"cell_id": new_id(IdKind.TASK, CLOCK)}, "cell_"),
    ],
)
def test_tool_request_bounds_and_ids(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(ToolRequest), **changes)


def test_tool_request_accepts_a_warden_started_request() -> None:
    request = _rebuild(_example(ToolRequest), worker_id=None, task_id=None, scope="HIVE")

    assert isinstance(request, ToolRequest)
    assert request.worker_id is None
    assert request.scope is ToolScope.HIVE


# ──────────────────────────────────────────────────────────────────────────────
# ToolPromoted
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_promoted_at_cell_scope_names_its_cell_and_warden() -> None:
    promoted = _rebuild(
        _example(ToolPromoted),
        scope="CELL",
        cell_id=new_id(IdKind.CELL, CLOCK),
        promoted_by_warden=new_id(IdKind.WARDEN, CLOCK),
        supersedes_version="0.9.0",
    )

    assert isinstance(promoted, ToolPromoted)
    assert promoted.scope is ToolScope.CELL
    assert promoted.supersedes_version == "0.9.0"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"scope": "CELL"}, "cell_id is required exactly when scope is CELL"),
        (
            {"scope": "CELL", "cell_id": new_id(IdKind.CELL, CLOCK)},
            "promoted_by_warden is required exactly when scope is CELL",
        ),
        (
            {"cell_id": new_id(IdKind.CELL, CLOCK)},
            "cell_id is required exactly when scope is CELL",
        ),
        (
            {"promoted_by_warden": new_id(IdKind.WARDEN, CLOCK)},
            "promoted_by_warden is required exactly when scope is CELL",
        ),
        ({"supported_os": ()}, "at least 1"),
        ({"supported_os": ("LINUX", "LINUX")}, "unique"),
        ({"input_schema_json": "[]"}, "JSON object"),
        ({"input_schema_json": '"schema"'}, "JSON object"),
        ({"input_schema_json": "not json"}, "not valid JSON"),
        ({"input_schema_json": DEEP_NESTING}, "not valid JSON"),
        (
            {"input_schema_json": '{"a": "' + "x" * MAX_INPUT_SCHEMA_CHARS + '"}'},
            f"at most {MAX_INPUT_SCHEMA_CHARS}",
        ),
        ({"description": "x" * (MAX_DESCRIPTION_CHARS + 1)}, f"at most {MAX_DESCRIPTION_CHARS}"),
        ({"package_sha256": SHA256.upper()}, "pattern"),
        ({"package_size_bytes": 0}, "greater than or equal to 1"),
        ({"supersedes_version": "1.0"}, "pattern"),
        ({"quarantined_on": new_id(IdKind.NODE, CLOCK)}, "cell_"),
        ({"quarantine_passed_at": datetime(2020, 1, 1)}, "timezone-aware"),  # naive on purpose
    ],
)
def test_tool_promoted_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(ToolPromoted), **changes)


def test_tool_promoted_keeps_its_schema_as_text() -> None:
    example = _example(ToolPromoted)
    assert isinstance(example, ToolPromoted)
    assert isinstance(example.input_schema_json, str)
    assert _rebuild(example, input_schema_json="{}")
