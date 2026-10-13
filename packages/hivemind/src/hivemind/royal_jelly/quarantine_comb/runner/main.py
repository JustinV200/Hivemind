"""Run the Quarantine Comb's in-sandbox runner: measure a tool and write one JSON report.

Invoked inside the sandbox as `python -m hivemind.royal_jelly.quarantine_comb.runner <package_dir>
<report_path> <limits_json>`, where `limits_json` is the text of a `QuarantineLimits` (not a
path). The runner sets up before the tool runs (a session on a scratch directory below the
sandbox root, OS resource limits, the audit hook, the wall deadline) and then drives the phases:
load the tool, run its tests, check its schema, fuzz it. It decides nothing: it writes the
measurements as a `RunnerReport` and exits 0, and the Comb's gates judge them. A non-zero exit
means the runner itself failed, which the Comb reports as a failed run.

The tests contract (what a generated `test_tool.py` may rely on): the runner puts the package
directory on `sys.path` so `from tool import run` works; it sets a module-level `SESSION` (a
`CellSession`) on the test module, before the module's own code runs and again after, so tests do
`asyncio.run(run(SESSION, {...}))`; it sets the environment variable `HIVEMIND_COMB_SCRATCH` to
the scratch directory the session is rooted in and points `tempfile` there. Nothing else is
available: no network, no process creation, no file access outside the package and the scratch.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Started by `QuarantineComb` through the sandbox; its report is read back by the same Comb.
    Calls into this package's runner modules, `hivemind.cell` and `hivemind.pheromone` (through
    `runner.session`) and `hivemind.llm` (the argument check): nothing above Layer 3.

Key invariants:
    - The report is written last, after the audit hook's recorder stops mattering and the
      deadline is disarmed, so nothing the tool does can interrupt or rewrite it.
    - Exit 0 whenever the report was written, even if every phase failed.
    - The tool's code runs only after `install_audit_hook`.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the runner's role.
    - hivemind.royal_jelly.quarantine_comb.runner.phases for the phases it drives.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import types
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from hivemind.royal_jelly.quarantine_comb.limits import fuzz_call_timeout, rlimit_plan
from hivemind.royal_jelly.quarantine_comb.runner.hooks import (
    AuditRecorder,
    build_policy,
    install_audit_hook,
)
from hivemind.royal_jelly.quarantine_comb.runner.inputs import derive_fuzz_inputs
from hivemind.royal_jelly.quarantine_comb.runner.phases import (
    ToolLoadError,
    check_schema,
    load_tool,
    run_fuzz,
    run_unittests,
)
from hivemind.royal_jelly.quarantine_comb.runner.report import (
    FuzzSection,
    RunnerReport,
    SchemaSection,
    UnittestSection,
)
from hivemind.royal_jelly.quarantine_comb.runner.session import (
    RecordingSession,
    open_scratch_session,
)
from hivemind.royal_jelly.quarantine_comb.runner.usage import (
    CountingWriter,
    DeadlineExceededError,
    Meter,
    apply_limits,
    arm_deadline,
    disarm_deadline,
    take_baseline,
)
from hivemind.royal_jelly.spec import SPEC_FILE, QuarantineLimits, ToolSpec
from waggle.clock import SystemClock

RUNNER_MODULE = "hivemind.royal_jelly.quarantine_comb.runner"  # What `python -m` is given.
SCRATCH_DIR_NAME = "scratch"  # Below the sandbox root: where the tool's session is rooted.
SCRATCH_ENV = "HIVEMIND_COMB_SCRATCH"  # Names the scratch directory for tests that want a path.
EXIT_OK = 0  # The report was written.
EXIT_BAD_USAGE = 2  # Wrong arguments or an unreadable package: nothing was measured.
_ARGUMENT_COUNT = 3  # package_dir, report_path, limits_json.

__all__ = [
    "EXIT_BAD_USAGE",
    "EXIT_OK",
    "RUNNER_MODULE",
    "SCRATCH_DIR_NAME",
    "SCRATCH_ENV",
    "main",
]


@dataclass(slots=True)
class _Outcome:
    """The sections the phases fill in; mutable because a deadline can stop the run midway.

    Owned by `_run_phases` alone: each phase assigns its section when it finishes, and a section
    still None at the end is exactly a phase the deadline cut off.
    """

    tests: UnittestSection | None = None
    schema: SchemaSection | None = None
    fuzz: FuzzSection | None = None
    timed_out: str = ""


def main(argv: Sequence[str]) -> int:
    """Measure the package named by `argv` and write the report.

    Args:
        argv: `package_dir`, `report_path` and the limits as JSON text.

    Returns:
        `EXIT_OK` when the report was written, `EXIT_BAD_USAGE` for bad arguments.
    """
    if len(argv) != _ARGUMENT_COUNT:
        sys.stderr.write("usage: runner <package_dir> <report_path> <limits_json>\n")
        return EXIT_BAD_USAGE
    package_dir, report_path = Path(argv[0]).resolve(), Path(argv[1])
    limits = QuarantineLimits.from_json(argv[2])
    spec = ToolSpec.from_json((package_dir / SPEC_FILE).read_text(encoding="utf-8"))
    root = report_path.resolve().parent
    scratch = root / SCRATCH_DIR_NAME
    scratch.mkdir(parents=True, exist_ok=True)
    # The session is built before anything is restricted: its set-up is the runner's own work.
    session = RecordingSession(asyncio.run(open_scratch_session(scratch, SystemClock())))
    os.environ[SCRATCH_ENV] = str(scratch)
    tempfile.tempdir = str(scratch)
    recorder = AuditRecorder(build_policy(package_dir, root))
    baseline = take_baseline()
    start = Meter.now()
    apply_limits(rlimit_plan(limits, baseline))
    install_audit_hook(recorder)
    arm_deadline(limits.wall_s)
    out = CountingWriter()
    outcome = _Outcome()
    try:
        _run_phases(package_dir, (spec, limits), (session, recorder, out), outcome)
    except DeadlineExceededError:
        outcome.timed_out = recorder.phase
    disarm_deadline()
    report = RunnerReport(
        tests=outcome.tests,
        schema=outcome.schema,
        fuzz=outcome.fuzz,
        usage=Meter.now().since(start, out.bytes_written, outcome.timed_out),
        violations=tuple(recorder.violations),
        calls=session.calls(),
    )
    report_path.write_text(report.model_dump_json(by_alias=True), encoding="utf-8")
    return EXIT_OK


def _run_phases(
    package_dir: Path,
    plan: tuple[ToolSpec, QuarantineLimits],
    tools: tuple[RecordingSession, AuditRecorder, CountingWriter],
    outcome: _Outcome,
) -> None:
    """Drive load, tests, schema and fuzz in order, filling `outcome` as each finishes."""
    spec, limits = plan
    session, recorder, out = tools
    # Loading comes first so an import error is reported once, as the tests gate's failure.
    recorder.phase = "load"
    try:
        tool = load_tool(package_dir)
    except ToolLoadError as error:
        outcome.tests = UnittestSection(ran=0, failures=(), error=str(error))
        return
    recorder.phase = "tests"
    outcome.tests = run_unittests(package_dir, session, out)
    recorder.phase = "schema"
    outcome.schema = check_schema(spec.input_schema_json)
    recorder.phase = "fuzz"
    outcome.fuzz = _fuzz(tool, spec, limits, (session, out))


def _fuzz(
    tool: types.ModuleType,
    spec: ToolSpec,
    limits: QuarantineLimits,
    channels: tuple[RecordingSession, CountingWriter],
) -> FuzzSection:
    """Derive inputs from the schema and fuzz the tool's entry point, counting what it prints."""
    session, out = channels
    schema = _schema_of(spec)
    entry = getattr(tool, spec.entry_point.split(":")[1], None)
    if entry is None:
        return FuzzSection(cases=0, clean_raises={}, failure="the entry point does not exist")
    inputs: list[dict[str, JsonValue]] = derive_fuzz_inputs(schema, limits.fuzz_cases)
    # Printing during fuzzing is counted, not forwarded: the Comb's own pipes stay quiet.
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        return asyncio.run(run_fuzz(entry, session, inputs, fuzz_call_timeout(limits)))


def _schema_of(spec: ToolSpec) -> dict[str, JsonValue]:
    """Parse the spec's schema text; the schema gate has already judged a bad one, so use {}."""
    parsed = json.loads(spec.input_schema_json)
    return parsed if isinstance(parsed, dict) else {}
