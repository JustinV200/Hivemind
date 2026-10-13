"""Test QuarantineComb's orchestration over a scripted sandbox: order, stops, failures, cleanup.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/comb.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/comb.py for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.royal_jelly import (
    StubSandbox,
    make_malicious_files,
    make_package,
    make_runner_report,
    make_tool_spec,
)

from hivemind.cell.session import CompletedCommand
from hivemind.royal_jelly.quarantine_comb import QuarantineComb, RunnerCall
from hivemind.royal_jelly.quarantine_comb.gates import GateContext, default_gates
from hivemind.royal_jelly.quarantine_comb.runner import RUNNER_MODULE
from hivemind.royal_jelly.quarantine_comb.sandbox import TIMED_OUT_EXIT_CODE
from hivemind.royal_jelly.spec import (
    GATE_ORDER,
    GateResult,
    QuarantineLimits,
    sha256_of,
    spec_sha256,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.tool.authoring import QuarantinePath

_LIMITS = QuarantineLimits(wall_s=20, fuzz_cases=4)


def _report_bytes(**overrides: object) -> bytes:
    """Serialise a runner report the way the runner writes it."""
    return make_runner_report(**overrides).model_dump_json(by_alias=True).encode()


def _done(code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> CompletedCommand:
    """Build a scripted runner exit."""
    return CompletedCommand(exit_code=code, stdout=stdout, stderr=stderr, duration_s=1.5)


async def test_a_passing_run_reports_all_six_gates_in_order() -> None:
    spec = make_tool_spec()
    package = make_package(spec=spec)
    cell_id = new_cell_id(FakeClock())
    sandbox = StubSandbox(report_json=_report_bytes(), cell_id=cell_id)

    report = await QuarantineComb(FakeClock()).run(package, spec, sandbox, _LIMITS)

    assert report.passed
    assert report.failing_gate is None
    assert tuple(gate.name for gate in report.gates) == GATE_ORDER
    assert report.package_sha256 == sha256_of(package)
    assert report.package_size_bytes == len(package)
    assert report.spec_sha256 == spec_sha256(spec)
    assert report.sandbox_cell_id == cell_id
    assert report.path is QuarantinePath.SANDBOX_CELL
    assert (report.tool_id, report.name, report.version) == (spec.tool_id, spec.name, spec.version)
    assert report.usage.wall_s == 0.5


async def test_the_package_is_put_under_pkg_and_the_runner_is_started_once() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(report_json=_report_bytes())

    await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert sorted(name for name in sandbox.files if name.startswith("pkg/")) == [
        "pkg/spec.json",
        "pkg/test_tool.py",
        "pkg/tool.py",
    ]
    assert len(sandbox.execs) == 1
    argv = sandbox.execs[0]
    assert argv[:3] == ("python3", "-m", RUNNER_MODULE)
    assert argv[3] == str(sandbox.root / "pkg")  # Rendered as this host renders paths.
    assert argv[4] == str(sandbox.root / "report.json")
    assert QuarantineLimits.from_json(argv[5]) == _LIMITS


async def test_the_runner_command_line_can_be_replaced() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(report_json=_report_bytes())

    def build(call: RunnerCall) -> tuple[str, ...]:
        return (call.interpreter, "run.py", str(call.package_dir))

    comb = QuarantineComb(FakeClock(), runner_argv_builder=build)
    await comb.run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert sandbox.execs == [("python3", "run.py", "/sandbox/pkg")]


async def test_a_package_the_static_gate_refuses_never_reaches_the_sandbox() -> None:
    spec = make_tool_spec()
    package = make_package(make_malicious_files("socket_import", spec))
    sandbox = StubSandbox(report_json=_report_bytes())

    report = await QuarantineComb(FakeClock()).run(package, spec, sandbox, _LIMITS)

    assert not report.passed
    assert report.failing_gate == "static"
    assert [gate.name for gate in report.gates] == ["static"]
    assert sandbox.execs == []
    assert sandbox.files == {}
    assert report.usage.wall_s == 0
    assert sandbox.cleaned == 1


async def test_bytes_that_are_not_a_package_fail_the_static_gate() -> None:
    spec = make_tool_spec()

    report = await QuarantineComb(FakeClock()).run(b"not a zip", spec, StubSandbox(), _LIMITS)

    assert report.failing_gate == "static"
    assert "cannot be unpacked" in report.gates[0].reason


async def test_the_first_failing_gate_ends_the_run_and_is_last_in_the_report() -> None:
    spec = make_tool_spec()
    fuzz = {"cases": 2, "clean_raises": {}, "failure": "case 1: timed out after 1s"}
    sandbox = StubSandbox(report_json=_report_bytes(fuzz=fuzz))

    report = await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert report.failing_gate == "fuzz"
    assert [gate.name for gate in report.gates] == ["static", "tests", "schema", "fuzz"]
    assert "timed out" in report.gates[-1].reason


async def test_output_captured_on_the_real_pipes_is_added_to_the_usage() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(
        report_json=_report_bytes(), result=_done(stdout=b"x" * 40, stderr=b"y" * 2)
    )

    report = await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert report.usage.output_bytes == 42


@pytest.mark.parametrize(
    ("exit_code", "gate", "needle"),
    [
        (TIMED_OUT_EXIT_CODE, "resources", "wall-clock limit"),
        (-9, "resources", "killed"),
        (137, "resources", "killed"),
        (1, "tests", "runner failed (exit 1)"),
    ],
)
async def test_a_runner_that_wrote_no_report_is_charged_to_the_right_gate(
    exit_code: int, gate: str, needle: str
) -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(result=_done(exit_code, stderr=b"Traceback: boom"))

    report = await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert not report.passed
    assert report.failing_gate == gate
    assert [g.name for g in report.gates] == ["static", gate]
    assert needle in report.gates[-1].reason
    assert report.usage.wall_s == 1.5


async def test_a_crashed_runners_stderr_is_in_the_detail_but_bounded() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(result=_done(1, stderr=b"E" * 100_000))

    report = await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert 0 < len(report.gates[-1].detail) <= 1_500


async def test_a_malformed_report_fails_the_tests_gate() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(report_json=json.dumps({"tests": 5}).encode())

    report = await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert report.failing_gate == "tests"
    assert "malformed" in report.gates[-1].reason


async def test_an_implausibly_large_report_is_refused() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(report_json=b" " * (600 * 1024))

    report = await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert report.failing_gate == "tests"
    assert "large" in report.gates[-1].reason


class _Exploding:
    """A gate with a bug: it raises instead of returning a verdict."""

    name = "static"
    uses_runner = False

    async def run(self, ctx: GateContext) -> GateResult:
        raise RuntimeError("gate bug")


async def test_the_sandbox_is_cleaned_up_even_when_a_gate_raises() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox()
    comb = QuarantineComb(FakeClock(), gates=[_Exploding()])

    with pytest.raises(RuntimeError, match="gate bug"):
        await comb.run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert sandbox.cleaned == 1


class _CleanupFails(StubSandbox):
    """A sandbox whose cleanup raises."""

    async def cleanup(self) -> None:
        await super().cleanup()
        raise OSError("disk gone")


async def test_a_failing_cleanup_does_not_replace_the_report() -> None:
    spec = make_tool_spec()
    sandbox = _CleanupFails(report_json=_report_bytes())

    report = await QuarantineComb(FakeClock()).run(make_package(spec=spec), spec, sandbox, _LIMITS)

    assert report.passed
    assert sandbox.cleaned == 1


async def test_the_report_records_the_path_it_was_asked_to() -> None:
    spec = make_tool_spec()
    sandbox = StubSandbox(report_json=_report_bytes())

    report = await QuarantineComb(FakeClock()).run(
        make_package(spec=spec), spec, sandbox, _LIMITS, QuarantinePath.REAL_CELL
    )

    assert report.path is QuarantinePath.REAL_CELL


async def test_a_shortened_gate_list_cannot_yield_a_passing_report() -> None:
    # The report model refuses a pass with a gate skipped, so a partial Comb cannot promote.
    spec = make_tool_spec()
    comb = QuarantineComb(FakeClock(), gates=default_gates()[:1])

    with pytest.raises(ValueError, match="exactly the gates"):
        await comb.run(make_package(spec=spec), spec, StubSandbox(), _LIMITS)
