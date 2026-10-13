"""Test the five runner-reading gates over hand-written runner output.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/gates/
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/gates/ for the gate modules under test.
"""

from __future__ import annotations

from typing import Any

import pytest
from builders.royal_jelly import make_gate_context, make_runner_report, make_tool_spec

from hivemind.royal_jelly.quarantine_comb.gates import (
    CapabilitiesGate,
    FuzzGate,
    ResourcesGate,
    SchemaGate,
    TestsGate,
)
from hivemind.royal_jelly.quarantine_comb.gates.capabilities import capability_text
from hivemind.royal_jelly.quarantine_comb.runner.report import SessionCall
from hivemind.royal_jelly.spec import GateResult, QuarantineLimits


def _violation(
    phase: str, detail: str = "started a process or shell (subprocess.Popen): ls"
) -> dict[str, Any]:
    """Build one violation record as the runner writes it."""
    return {"kind": "process", "detail": detail, "phase": phase, "location": "tool.py:7"}


# ── tests gate ──────────────────────────────────────────────────────────────────


async def test_tests_gate_passes_when_tests_ran_and_none_failed() -> None:
    result = await TestsGate().run(make_gate_context(runner=make_runner_report()))

    assert result.passed
    assert result.name == "tests"


@pytest.mark.parametrize(
    ("section", "needle"),
    [
        (None, "did not finish"),
        ({"ran": 0, "failures": []}, "ran no tests"),
        (
            {"ran": 3, "failures": ["ToolTest.test_a: AssertionError: 1 != 2"]},
            "1 of 3 tests failed",
        ),
        (
            {"ran": 0, "failures": [], "error": "tool.py failed to import: SyntaxError"},
            "failed to import",
        ),
    ],
)
async def test_tests_gate_fails_with_a_reason(section: dict[str, Any] | None, needle: str) -> None:
    ctx = make_gate_context(runner=make_runner_report(tests=section))

    result = await TestsGate().run(ctx)

    assert not result.passed
    assert needle in result.reason


@pytest.mark.parametrize("phase", ["load", "tests"])
async def test_tests_gate_names_a_violation_from_loading_or_testing(phase: str) -> None:
    runner = make_runner_report(violations=[_violation(phase)])

    result = await TestsGate().run(make_gate_context(runner=runner))

    assert not result.passed
    assert "started a process" in result.reason
    assert "tool.py:7" in result.reason


# ── schema gate ─────────────────────────────────────────────────────────────────


async def test_schema_gate_passes_a_closed_schema() -> None:
    assert (await SchemaGate().run(make_gate_context(runner=make_runner_report()))).passed


@pytest.mark.parametrize(
    ("section", "needle"),
    [
        (None, "did not finish"),
        (
            {
                "compiled": False,
                "sample_accepted": False,
                "extra_refused": False,
                "findings": ["bad"],
            },
            "does not compile",
        ),
        (
            {"compiled": True, "sample_accepted": False, "extra_refused": True, "findings": []},
            "sample input",
        ),
        (
            {"compiled": True, "sample_accepted": True, "extra_refused": False, "findings": []},
            "additionalProperties",
        ),
    ],
)
async def test_schema_gate_fails_with_a_reason(section: dict[str, Any] | None, needle: str) -> None:
    ctx = make_gate_context(runner=make_runner_report(schema=section))

    result = await SchemaGate().run(ctx)

    assert not result.passed
    assert needle in result.reason


# ── fuzz gate ───────────────────────────────────────────────────────────────────


async def test_fuzz_gate_passes_and_summarises_the_clean_raises() -> None:
    runner = make_runner_report(fuzz={"cases": 8, "clean_raises": {"ValueError": 3}, "failure": ""})

    result = await FuzzGate().run(make_gate_context(runner=runner))

    assert result.passed
    assert "ValueError x3" in result.detail


async def test_fuzz_gate_fails_with_the_runners_failure() -> None:
    fuzz = {"cases": 2, "clean_raises": {}, "failure": "case 1: timed out after 1s"}

    result = await FuzzGate().run(make_gate_context(runner=make_runner_report(fuzz=fuzz)))

    assert not result.passed
    assert "timed out" in result.reason


async def test_fuzz_gate_fails_when_fuzzing_never_finished() -> None:
    result = await FuzzGate().run(make_gate_context(runner=make_runner_report(fuzz=None)))

    assert not result.passed
    assert "did not finish" in result.reason


async def test_fuzz_gate_names_a_violation_the_hook_recorded_while_fuzzing() -> None:
    runner = make_runner_report(violations=[_violation("fuzz")])

    result = await FuzzGate().run(make_gate_context(runner=runner))

    assert not result.passed
    assert "subprocess.Popen" in result.reason


async def test_a_violation_in_one_phase_does_not_fail_another_phases_gate() -> None:
    runner = make_runner_report(violations=[_violation("fuzz")])

    assert (await TestsGate().run(make_gate_context(runner=runner))).passed
    assert (await SchemaGate().run(make_gate_context(runner=runner))).passed


# ── resources gate ──────────────────────────────────────────────────────────────

_LIMITS = QuarantineLimits(cpu_s=2, memory_bytes=64 * 1024 * 1024, wall_s=5, output_bytes=2_048)


def _usage(**overrides: object) -> dict[str, Any]:
    """Build a usage section inside the limits above, with fields replaced."""
    base = {"cpu_s": 0.5, "peak_rss_bytes": 1_000, "wall_s": 1.0, "output_bytes": 10}
    return {**base, **overrides}


async def test_resources_gate_passes_inside_the_limits() -> None:
    ctx = make_gate_context(runner=make_runner_report(usage=_usage()), limits=_LIMITS)

    assert (await ResourcesGate().run(ctx)).passed


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"cpu_s": 2.5}, "CPU"),
        ({"peak_rss_bytes": 65 * 1024 * 1024}, "memory"),
        ({"wall_s": 6.0}, "wall time"),
        ({"output_bytes": 2_048}, "output"),
        ({"timed_out_phase": "fuzz"}, "fuzz phase"),
    ],
)
async def test_resources_gate_fails_on_each_exceeded_limit(
    overrides: dict[str, Any], needle: str
) -> None:
    ctx = make_gate_context(runner=make_runner_report(usage=_usage(**overrides)), limits=_LIMITS)

    result = await ResourcesGate().run(ctx)

    assert not result.passed
    assert needle in result.reason


# ── capabilities gate ───────────────────────────────────────────────────────────


def _call(op: str, target: str) -> dict[str, Any]:
    """Build one recorded session call."""
    return {"op": op, "target": target, "count": 1}


async def _capabilities(declared: tuple[str, ...], calls: list[dict[str, Any]]) -> GateResult:
    """Run the capabilities gate for a spec declaring `declared` and a runner that made `calls`."""
    spec = make_tool_spec(required_capabilities=declared)
    return await CapabilitiesGate().run(
        make_gate_context(spec, runner=make_runner_report(calls=calls))
    )


async def test_capabilities_gate_passes_with_no_calls() -> None:
    assert (await _capabilities(("net:example.org",), [])).passed


async def test_capabilities_gate_fails_naming_an_exec_the_spec_does_not_declare() -> None:
    result = await _capabilities(("fs:read:*",), [_call("exec", "ls")])

    assert not result.passed
    assert "exec:ls" in result.reason


async def test_capabilities_gate_passes_when_the_exec_is_declared() -> None:
    assert (await _capabilities(("fs:read:*", "exec:ls"), [_call("exec", "ls")])).passed


@pytest.mark.parametrize(
    ("declared", "call", "passes"),
    [
        (("fs:read:*",), _call("get_file", "notes/a.txt"), True),
        (("fs:read:notes/*",), _call("get_file", "notes/a.txt"), True),
        (("fs:read:notes/*",), _call("get_file", "other/a.txt"), False),
        (("fs:read:*",), _call("put_file", "out.txt"), False),
        (("fs:write:out.txt",), _call("put_file", "out.txt"), True),
        (("fs:write:out.txt",), _call("delete_file", "out.txt"), True),
        (("exec:*",), _call("start", "python3"), True),
        (("exec:ls",), _call("exec", "rm"), False),
        (("fs:read:notes/*",), _call("get_file", "notes/a[*]b"), True),
        (("fs:read:notes/a.txt",), _call("get_file", "notes/*"), False),
    ],
)
async def test_capabilities_gate_maps_each_session_verb_to_its_capability(
    declared: tuple[str, ...], call: dict[str, Any], passes: bool
) -> None:
    assert (await _capabilities(declared, [call])).passed is passes


async def test_capabilities_gate_stops_at_the_first_uncovered_call() -> None:
    calls = [_call("exec", "ls"), _call("exec", "cat"), _call("exec", "rm")]

    result = await _capabilities(("exec:ls",), calls)

    assert "exec:cat" in result.reason
    assert "exec:rm" not in result.reason


async def test_capabilities_gate_rejects_an_unknown_verb_rather_than_passing_it() -> None:
    result = await _capabilities(("exec:*",), [_call("teleport", "x")])

    assert not result.passed


def test_capability_text_renders_the_declaration_a_spec_would_write() -> None:
    assert capability_text(SessionCall(op="exec", target="ls", count=1)) == "exec:ls"
    assert capability_text(SessionCall(op="put_file", target="a/b", count=2)) == "fs:write:a/b"
