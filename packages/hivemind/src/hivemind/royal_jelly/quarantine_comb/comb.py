"""Provide QuarantineComb: run a tool package through the six gates and produce its report.

The Quarantine Comb is the proof a new tool must have before the Comb Registry will promote it.
`QuarantineComb.run` unpacks the package (a package that cannot be unpacked fails the static
gate), runs the pure static gate first, then puts the files into the sandbox, runs the in-sandbox
runner once, reads the one JSON report it writes and lets the remaining gates judge its sections,
in `GATE_ORDER`. The first failing gate ends the run: a failing report lists the gates that ran,
the failing one last; a passing report lists all six. The sandbox is always cleaned up, whatever
happened. The Comb records nothing on the Pheromone Trail: it returns the report and the caller
(the Warden that drove it, or `hive tools quarantine`) trails and relays it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by the Warden of the sandbox Cell (ADR-0047) over its `SandboxCell`, and by the CLI over
    a `SandboxSubprocess`. Calls into the gates, a `Sandbox`, the runner's report model and
    `hivemind.royal_jelly.spec`.

Key invariants:
    - The report's gates are those that ran, in order, ending at the first failure; `passed` is
      true only when every gate passed (a six-gate sequence, enforced by the report model).
    - `sandbox.cleanup()` runs in a `finally`, and a cleanup failure never replaces the report.
    - The runner is executed at most once per run, and only if some gate reads its report.
    - Nothing is written to the trail; time comes from the injected clock.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the order and the single-failure rule.
    - hivemind.royal_jelly.spec.report for QuarantineReport.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import ValidationError

from hivemind.cell.session import CompletedCommand
from hivemind.common.logging import get_logger
from hivemind.royal_jelly.errors import PackageError
from hivemind.royal_jelly.quarantine_comb.gates import Gate, GateContext, default_gates
from hivemind.royal_jelly.quarantine_comb.gates.base import bounded, fail
from hivemind.royal_jelly.quarantine_comb.runner import RUNNER_MODULE, RunnerReport
from hivemind.royal_jelly.quarantine_comb.sandbox import TIMED_OUT_EXIT_CODE, Sandbox
from hivemind.royal_jelly.spec import (
    GateResult,
    QuarantineLimits,
    QuarantineReport,
    ResourceUsage,
    ToolSpec,
    sha256_of,
    spec_sha256,
    unpack,
)
from waggle.clock import Clock
from waggle.messages.tool.authoring import QuarantinePath

PACKAGE_DIR = Path("pkg")  # Where the files are put, below the sandbox root.
REPORT_FILE = Path("report.json")  # Where the runner writes, below the sandbox root.
EXEC_GRACE_S = 30.0  # Added to the wall limit: interpreter start-up and the runner's teardown.
MAX_REPORT_BYTES = 512 * 1024  # A runner report is a few KiB; more is a runner gone wrong.
_STDERR_EXCERPT_CHARS = 1_500  # How much of a crashed runner's stderr goes in a gate detail.
_KILLED_EXIT_CODES = frozenset({137, 152, 153})  # 128 plus SIGKILL, SIGXCPU, SIGXFSZ.

log = get_logger(__name__)

__all__ = ["EXEC_GRACE_S", "PACKAGE_DIR", "REPORT_FILE", "QuarantineComb", "RunnerCall"]


@dataclass(frozen=True, slots=True)
class RunnerCall:
    """What a runner command line is built from.

    Attributes:
        interpreter: The sandbox's Python command.
        package_dir: Absolute directory the files were put in.
        report_path: Absolute path the runner writes its report to.
        limits_json: The limits as JSON text (the third argument, not a path).
    """

    interpreter: str
    package_dir: Path
    report_path: Path
    limits_json: str


RunnerArgvBuilder = Callable[[RunnerCall], tuple[str, ...]]


def default_runner_argv(call: RunnerCall) -> tuple[str, ...]:
    """Build `python -m <runner module> <package_dir> <report_path> <limits_json>`."""
    return (
        call.interpreter,
        "-m",
        RUNNER_MODULE,
        str(call.package_dir),
        str(call.report_path),
        call.limits_json,
    )


@dataclass(frozen=True, slots=True)
class _Judged:
    """The gates that ran and what the run spent."""

    results: tuple[GateResult, ...]
    usage: ResourceUsage


class QuarantineComb:
    """Run a package through the gates inside a sandbox and report the verdict."""

    def __init__(
        self,
        clock: Clock,
        *,
        gates: Sequence[Gate] | None = None,
        runner_argv_builder: RunnerArgvBuilder | None = None,
    ) -> None:
        """Build a Comb.

        Args:
            clock: Stamps the report's start and finish; never `datetime.now()`.
            gates: The gates to run, in order; the six default gates when omitted. A sequence
                other than the six cannot produce a passing report (the report model refuses it),
                which tests use to run the runner without the static gate.
            runner_argv_builder: Builds the runner's command line; `python -m` by default.
        """
        self._clock = clock
        self._gates = tuple(gates) if gates is not None else default_gates()
        self._argv = runner_argv_builder or default_runner_argv

    async def run(
        self,
        package: bytes,
        spec: ToolSpec,
        sandbox: Sandbox,
        limits: QuarantineLimits,
        path: QuarantinePath = QuarantinePath.SANDBOX_CELL,
    ) -> QuarantineReport:
        """Prove `package` against `spec` in `sandbox` and return the verdict.

        Args:
            package: The tool package archive bytes (untrusted).
            spec: The spec the package claims to implement.
            sandbox: Where the runner executes; cleaned up before this returns.
            limits: What the run may spend.
            path: The route this run is (the report records it; the Real Cell leg of the
                non-Ubuntu route is the caller's to arrange).

        Returns:
            The report: failing at the first failed gate, passing only if all six passed.
        """
        started = self._clock.now()
        try:
            judged = await self._judge(package, spec, sandbox, limits)
        finally:
            await _cleanup(sandbox)
        failed = next((result for result in judged.results if not result.passed), None)
        if failed is not None:
            log.info("comb.gate_failed", tool=spec.name, gate=failed.name, reason=failed.reason)
        return QuarantineReport(
            tool_id=spec.tool_id,
            name=spec.name,
            version=spec.version,
            spec_sha256=spec_sha256(spec),
            package_sha256=sha256_of(package),
            # An empty package already failed the static gate; the model needs a positive size.
            package_size_bytes=max(1, len(package)),
            path=path,
            sandbox_cell_id=sandbox.cell_id,
            started_at=started,
            finished_at=self._clock.now(),
            gates=judged.results,
            passed=failed is None,
            failing_gate=failed.name if failed else None,
            usage=judged.usage,
        )

    async def _judge(
        self, package: bytes, spec: ToolSpec, sandbox: Sandbox, limits: QuarantineLimits
    ) -> _Judged:
        """Unpack, then run the gates in order until one fails."""
        try:
            files = unpack(package)
        except PackageError as error:
            reason = f"the package cannot be unpacked: {error}"
            return _Judged((fail("static", reason),), _zero_usage(0.0, 0))
        ctx = GateContext(spec=spec, files=files, package=package, sandbox=sandbox, limits=limits)
        results: list[GateResult] = []
        usage = _zero_usage(0.0, 0)
        for gate in self._gates:
            # The runner is started lazily, by the first gate that reads its report, so a package
            # the static gate refuses never reaches the sandbox.
            if gate.uses_runner and ctx.runner is None:
                outcome = await self._execute_runner(ctx)
                if isinstance(outcome, _RunnerFailure):
                    results.append(outcome.result)
                    return _Judged(tuple(results), outcome.usage)
                ctx = replace(ctx, runner=outcome)
                usage = _usage_of(outcome)
            result = await gate.run(ctx)
            results.append(result)
            if not result.passed:
                break
        return _Judged(tuple(results), usage)

    async def _execute_runner(self, ctx: GateContext) -> RunnerReport | _RunnerFailure:
        """Put the package into the sandbox, run the runner and read its report."""
        sandbox = ctx.sandbox
        for name, data in ctx.files.items():
            await sandbox.put(PACKAGE_DIR / name, data)
        call = RunnerCall(
            interpreter=sandbox.interpreter,
            package_dir=sandbox.root / PACKAGE_DIR,
            report_path=sandbox.root / REPORT_FILE,
            limits_json=ctx.limits.to_json(),
        )
        # An external await bounded by the wall limit plus a grace; on timeout the sandbox kills
        # the process group and reports TIMED_OUT_EXIT_CODE.
        completed = await sandbox.exec(self._argv(call), ctx.limits.wall_s + EXEC_GRACE_S)
        try:
            raw = await sandbox.read(REPORT_FILE)
        except FileNotFoundError:
            return _runner_died(completed, ctx.limits)
        if len(raw) > MAX_REPORT_BYTES:
            return _RunnerFailure(
                fail("tests", "the runner's report is implausibly large"), _spent(completed)
            )
        try:
            report = RunnerReport.model_validate_json(raw)
        except ValidationError as error:
            return _RunnerFailure(
                fail("tests", "the runner's report is malformed", bounded(str(error), 1_000)),
                _spent(completed),
            )
        # What was captured on the real pipes was not routed through the tool's counted stdout.
        extra = len(completed.stdout) + len(completed.stderr)
        usage = report.usage.model_copy(update={"output_bytes": report.usage.output_bytes + extra})
        return report.model_copy(update={"usage": usage})


@dataclass(frozen=True, slots=True)
class _RunnerFailure:
    """The runner never produced a report; the gate it is charged to and what it spent."""

    result: GateResult
    usage: ResourceUsage


async def _cleanup(sandbox: Sandbox) -> None:
    """Clean the sandbox; a failure is logged, never raised, because the verdict still stands."""
    try:
        await sandbox.cleanup()
    except Exception as error:  # SAFETY: any cleanup failure must not mask a report.
        log.warning("comb.cleanup_failed", error=type(error).__name__)


def _runner_died(completed: CompletedCommand, limits: QuarantineLimits) -> _RunnerFailure:
    """Charge a missing report to the right gate: a limit kill to resources, a crash to tests."""
    code = completed.exit_code
    if code == TIMED_OUT_EXIT_CODE:
        reason = f"the run exceeded the {limits.wall_s:g}s wall-clock limit and was killed"
        return _RunnerFailure(fail("resources", reason), _spent(completed))
    if code < 0 or code in _KILLED_EXIT_CODES:
        reason = f"the runner was killed (exit {code}), most likely by a CPU, memory or file limit"
        return _RunnerFailure(fail("resources", reason), _spent(completed))
    excerpt = completed.stderr.decode("utf-8", errors="replace")[-_STDERR_EXCERPT_CHARS:]
    return _RunnerFailure(
        fail("tests", f"the runner failed (exit {code})", excerpt), _spent(completed)
    )


def _spent(completed: CompletedCommand) -> ResourceUsage:
    """Usage for a run with no report: only wall time and captured output are known."""
    return _zero_usage(completed.duration_s, len(completed.stdout) + len(completed.stderr))


def _zero_usage(wall_s: float, output_bytes: int) -> ResourceUsage:
    """Build a usage record that knows only wall time and output."""
    return ResourceUsage(cpu_s=0.0, peak_rss_bytes=0, wall_s=wall_s, output_bytes=output_bytes)


def _usage_of(report: RunnerReport) -> ResourceUsage:
    """Convert the runner's usage section to the report model's."""
    usage = report.usage
    return ResourceUsage(
        cpu_s=usage.cpu_s,
        peak_rss_bytes=usage.peak_rss_bytes,
        wall_s=usage.wall_s,
        output_bytes=usage.output_bytes,
    )
