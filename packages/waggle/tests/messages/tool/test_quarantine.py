"""Tests for the tool family's quarantine half (tool.quarantine): the order, its report, the retire.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins, for ToolQuarantine, ToolQuarantineReport and
    ToolRetired, construction, the JSON-object checks on the three JSON fields at their bounds,
    the scope rule (a Cell exactly for CELL scope) in both directions, the failing-gate rule in
    both directions, and the id and pattern checks spec section 8.8 names. The family's EXAMPLES
    tuple, the round trip and the shared bounds live in test_authoring.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.tool.quarantine for the module under test.
    - test_authoring.py for EXAMPLES and the round trip of every tool class.
    - docs/waggle/spec.md section 8.8 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import WaggleMessage
from waggle.messages.tool.authoring import QuarantinePath, ToolScope
from waggle.messages.tool.quarantine import (
    MAX_LIMITS_CHARS,
    MAX_REPORT_CHARS,
    MAX_SPEC_CHARS,
    ToolQuarantine,
    ToolQuarantineReport,
    ToolRetired,
)

CLOCK = FakeClock()
SHA256 = "cd" * 32  # 32 digest bytes as 64 lowercase hex characters.
DEEP_NESTING = "[" * 5_000  # Deeper than the JSON decoder's recursion limit, well under every cap.
QUARANTINE = ToolQuarantine(
    tool_id=new_id(IdKind.TOOL, CLOCK),
    name="csv_profile",
    version="1.0.0",
    scope=ToolScope.HIVE,
    cell_id=None,
    spec_json='{"name": "csv_profile"}',
    package_sha256=SHA256,
    package_size_bytes=2_048,
    path=QuarantinePath.SANDBOX_CELL,
    limits_json='{"cpu_s": 30}',
    requested_by_warden=new_id(IdKind.WARDEN, CLOCK),
    reason="Prove the scaffold before promotion.",
)
REPORT = ToolQuarantineReport(
    tool_id=new_id(IdKind.TOOL, CLOCK),
    version="1.0.0",
    passed=True,
    path=QuarantinePath.SANDBOX_CELL,
    sandbox_cell_id=new_id(IdKind.CELL, CLOCK),
    failing_gate=None,
    report_json='{"gates": []}',
    reason="Every gate passed.",
)
RETIRED = ToolRetired(
    tool_id=new_id(IdKind.TOOL, CLOCK),
    name="csv_profile",
    version="1.0.0",
    scope=ToolScope.HIVE,
    cell_id=None,
    retired_by=new_id(IdKind.HIVE, CLOCK),
    reason="Superseded.",
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _json_padded(limit: int) -> str:
    """A JSON object text of exactly ``limit`` characters, the largest the field allows."""
    # Two braces, the key and its quotes and colon take 8 characters; the value fills the rest.
    return '{"a": "' + "x" * (limit - 9) + '"}'


# ──────────────────────────────────────────────────────────────────────────────
# ToolQuarantine
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_quarantine_at_cell_scope_names_its_cell() -> None:
    order = _rebuild(QUARANTINE, scope="CELL", cell_id=new_id(IdKind.CELL, CLOCK), path="REAL_CELL")

    assert isinstance(order, ToolQuarantine)
    assert order.scope is ToolScope.CELL
    assert order.path is QuarantinePath.REAL_CELL


@pytest.mark.parametrize(
    "changes",
    [{"scope": "CELL"}, {"cell_id": new_id(IdKind.CELL, CLOCK)}],
    ids=["cell scope without a cell", "hive scope with a cell"],
)
def test_tool_quarantine_requires_a_cell_exactly_for_cell_scope(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="cell_id is required exactly when scope is CELL"):
        _rebuild(QUARANTINE, **changes)


@pytest.mark.parametrize(
    ("field", "limit"),
    [("spec_json", MAX_SPEC_CHARS), ("limits_json", MAX_LIMITS_CHARS)],
)
def test_tool_quarantine_json_fields_hold_a_full_size_object_and_refuse_one_more(
    field: str, limit: int
) -> None:
    assert _rebuild(QUARANTINE, **{field: _json_padded(limit)})
    with pytest.raises(ValidationError, match=f"at most {limit}"):
        _rebuild(QUARANTINE, **{field: _json_padded(limit + 1)})


@pytest.mark.parametrize("field", ["spec_json", "limits_json"])
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("[]", "JSON object"),
        ('"spec"', "JSON object"),
        ("null", "JSON object"),
        ("not json", "not valid JSON"),
        ("", "not valid JSON"),
        (DEEP_NESTING, "not valid JSON"),
    ],
)
def test_tool_quarantine_json_fields_must_parse_as_an_object(
    field: str, text: str, reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(QUARANTINE, **{field: text})


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"package_sha256": SHA256.upper()}, "pattern"),
        ({"package_sha256": "ab"}, "pattern"),
        ({"package_size_bytes": 0}, "greater than or equal to 1"),
        ({"version": "1.0"}, "pattern"),
        ({"name": "Csv-Profile"}, "pattern"),
        ({"path": "SANDBOX"}, "path"),
        ({"tool_id": new_id(IdKind.TASK, CLOCK)}, "tool_"),
        ({"requested_by_warden": new_id(IdKind.WORKER, CLOCK)}, "warden_"),
        ({"cell_id": new_id(IdKind.TASK, CLOCK), "scope": "CELL"}, "cell_"),
    ],
)
def test_tool_quarantine_bounds_and_ids(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(QUARANTINE, **changes)


# ──────────────────────────────────────────────────────────────────────────────
# ToolQuarantineReport
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_quarantine_report_names_the_failing_gate_when_it_did_not_pass() -> None:
    failed = _rebuild(REPORT, passed=False, failing_gate="static")

    assert isinstance(failed, ToolQuarantineReport)
    assert failed.failing_gate == "static"


@pytest.mark.parametrize(
    "changes",
    [{"passed": False}, {"failing_gate": "fuzz"}],
    ids=["failed without a gate", "passed with a gate"],
)
def test_tool_quarantine_report_failing_gate_is_none_exactly_when_passed(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="failing_gate is None exactly when passed"):
        _rebuild(REPORT, **changes)


def test_tool_quarantine_report_holds_a_full_size_report_and_refuses_one_more() -> None:
    assert _rebuild(REPORT, report_json=_json_padded(MAX_REPORT_CHARS))
    with pytest.raises(ValidationError, match=f"at most {MAX_REPORT_CHARS}"):
        _rebuild(REPORT, report_json=_json_padded(MAX_REPORT_CHARS + 1))


@pytest.mark.parametrize("text", ["[]", "7", "not json", DEEP_NESTING])
def test_tool_quarantine_report_json_must_parse_as_an_object(text: str) -> None:
    with pytest.raises(ValidationError, match=r"JSON object|not valid JSON"):
        _rebuild(REPORT, report_json=text)


@pytest.mark.parametrize("gate", ["", "Static", "1gate", "a-b", "g" * 65])
def test_tool_quarantine_report_gate_name_is_a_snake_case_word(gate: str) -> None:
    with pytest.raises(ValidationError, match=r"pattern|at most"):
        _rebuild(REPORT, passed=False, failing_gate=gate)


@pytest.mark.parametrize(
    "changes",
    [
        {"sandbox_cell_id": new_id(IdKind.NODE, CLOCK)},
        {"tool_id": new_id(IdKind.CELL, CLOCK)},
        {"version": "v1"},
    ],
)
def test_tool_quarantine_report_ids_and_version_are_checked(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _rebuild(REPORT, **changes)


# ──────────────────────────────────────────────────────────────────────────────
# ToolRetired
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_retired_accepts_the_queen_or_a_warden_as_retirer() -> None:
    by_queen = _rebuild(RETIRED, retired_by=new_id(IdKind.HIVE, CLOCK))
    by_warden = _rebuild(
        RETIRED,
        scope="CELL",
        cell_id=new_id(IdKind.CELL, CLOCK),
        retired_by=new_id(IdKind.WARDEN, CLOCK),
    )

    assert isinstance(by_queen, ToolRetired)
    assert isinstance(by_warden, ToolRetired)
    assert by_warden.scope is ToolScope.CELL


@pytest.mark.parametrize("kind", [IdKind.WORKER, IdKind.DEVICE, IdKind.CELL])
def test_tool_retired_refuses_any_other_bee_as_retirer(kind: IdKind) -> None:
    with pytest.raises(ValidationError, match="accepted prefixes"):
        _rebuild(RETIRED, retired_by=new_id(kind, CLOCK))


@pytest.mark.parametrize(
    "changes",
    [{"scope": "CELL"}, {"cell_id": new_id(IdKind.CELL, CLOCK)}],
    ids=["cell scope without a cell", "hive scope with a cell"],
)
def test_tool_retired_requires_a_cell_exactly_for_cell_scope(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="cell_id is required exactly when scope is CELL"):
        _rebuild(RETIRED, **changes)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"version": "1.0"}, "pattern"),
        ({"name": ""}, "pattern"),
        ({"tool_id": new_id(IdKind.WARDEN, CLOCK)}, "tool_"),
    ],
)
def test_tool_retired_bounds_and_ids(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(RETIRED, **changes)
