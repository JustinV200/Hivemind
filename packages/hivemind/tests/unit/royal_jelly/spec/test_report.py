"""Tests for hivemind.royal_jelly.spec.report: the report's validators, limits and round trip.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/spec/report.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.spec.report for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.royal_jelly import make_failing_report, make_passing_report, make_tool_spec
from pydantic import ValidationError

from hivemind.royal_jelly.spec import (
    GATE_ORDER,
    GateResult,
    QuarantineLimits,
    QuarantineReport,
    ResourceUsage,
    spec_sha256,
)
from waggle.messages.tool.authoring import QuarantinePath

_SHA = "a" * 64


def test_a_passing_report_round_trips_through_json() -> None:
    report = make_passing_report(make_tool_spec(), _SHA)

    assert QuarantineReport.from_json(report.to_json()) == report
    assert report.passed
    assert report.failing_gate is None
    assert tuple(g.name for g in report.gates) == GATE_ORDER


def test_a_failing_report_names_the_first_failed_gate_and_stops_there() -> None:
    report = make_failing_report(make_tool_spec(), _SHA, failing_gate="fuzz")

    assert not report.passed
    assert report.failing_gate == "fuzz"
    assert [g.name for g in report.gates] == ["static", "tests", "schema", "fuzz"]
    assert QuarantineReport.from_json(report.to_json()) == report


def test_the_report_records_the_spec_hash() -> None:
    spec = make_tool_spec()

    assert make_passing_report(spec, _SHA).spec_sha256 == spec_sha256(spec)


def test_passed_must_equal_all_gates_passed() -> None:
    report = make_failing_report(make_tool_spec(), _SHA)

    with pytest.raises(ValidationError, match="passed"):
        QuarantineReport.model_validate({**report.model_dump(), "passed": True})


def test_a_passed_report_cannot_hide_a_failed_gate() -> None:
    passing = make_passing_report(make_tool_spec(), _SHA)
    gates = (*passing.gates[:-1], GateResult(name="capabilities", passed=False, reason="x"))

    with pytest.raises(ValidationError, match="passed"):
        QuarantineReport.model_validate({**passing.model_dump(), "gates": gates})


def test_failing_gate_must_be_the_first_failed_gate() -> None:
    report = make_failing_report(make_tool_spec(), _SHA, failing_gate="tests")

    with pytest.raises(ValidationError, match="failing_gate"):
        QuarantineReport.model_validate({**report.model_dump(), "failing_gate": "static"})
    with pytest.raises(ValidationError, match="failing_gate"):
        passing = make_passing_report(make_tool_spec(), _SHA)
        QuarantineReport.model_validate({**passing.model_dump(), "failing_gate": "tests"})


def test_a_passing_report_must_hold_every_gate_in_order() -> None:
    passing = make_passing_report(make_tool_spec(), _SHA)

    with pytest.raises(ValidationError, match="exactly the gates"):
        QuarantineReport.model_validate({**passing.model_dump(), "gates": passing.gates[:3]})
    with pytest.raises(ValidationError, match="exactly the gates"):
        QuarantineReport.model_validate(
            {**passing.model_dump(), "gates": tuple(reversed(passing.gates))}
        )


def test_gate_names_must_be_unique() -> None:
    failing = make_failing_report(make_tool_spec(), _SHA, failing_gate="tests")
    duplicated = (*failing.gates, failing.gates[0])

    with pytest.raises(ValidationError, match="unique"):
        QuarantineReport.model_validate({**failing.model_dump(), "gates": duplicated})


def test_a_report_with_no_gates_is_refused() -> None:
    passing = make_passing_report(make_tool_spec(), _SHA)

    with pytest.raises(ValidationError):
        QuarantineReport.model_validate({**passing.model_dump(), "gates": ()})


def test_finished_must_not_precede_started() -> None:
    passing = make_passing_report(make_tool_spec(), _SHA)

    with pytest.raises(ValidationError, match="finished_at"):
        QuarantineReport.model_validate(
            {**passing.model_dump(), "finished_at": passing.started_at - timedelta(seconds=1)}
        )


@pytest.mark.parametrize("field", ["spec_sha256", "package_sha256"])
def test_hash_fields_must_be_lowercase_sha256_hex(field: str) -> None:
    passing = make_passing_report(make_tool_spec(), _SHA)

    with pytest.raises(ValidationError):
        QuarantineReport.model_validate({**passing.model_dump(), field: "XYZ"})


def test_package_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_passing_report(make_tool_spec(), _SHA, 0)


def test_the_report_path_and_review_flag_are_recorded() -> None:
    report = make_passing_report(
        make_tool_spec(), _SHA, path=QuarantinePath.REAL_CELL, reviewed=True
    )

    assert report.path is QuarantinePath.REAL_CELL
    assert report.reviewed
    assert not make_passing_report(make_tool_spec(), _SHA).reviewed


def test_a_subprocess_sandbox_report_has_no_cell() -> None:
    assert make_passing_report(make_tool_spec(), _SHA, sandbox_cell_id=None).sandbox_cell_id is None


def test_quarantine_limits_have_the_designed_defaults() -> None:
    limits = QuarantineLimits()

    assert limits.cpu_s == 30
    assert limits.memory_bytes == 512 * 1024 * 1024
    assert limits.wall_s == 120
    assert limits.output_bytes == 1024 * 1024
    assert limits.fuzz_cases == 32


def test_quarantine_limits_round_trip_and_reject_nonsense() -> None:
    limits = QuarantineLimits(cpu_s=5, fuzz_cases=8)

    assert QuarantineLimits.from_json(limits.to_json()) == limits
    for bad in ({"cpu_s": 0}, {"fuzz_cases": 0}, {"memory_bytes": 1}, {"wall_s": -1}):
        with pytest.raises(ValidationError):
            QuarantineLimits(**bad)


def test_resource_usage_rejects_negatives() -> None:
    with pytest.raises(ValidationError):
        ResourceUsage(cpu_s=-1, peak_rss_bytes=0, wall_s=0, output_bytes=0)
