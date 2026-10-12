"""Run the tool's phases inside the sandbox: load it, run its tests, check its schema, fuzz it.

Each function here does one measured piece of work and returns the report section for it; none
decides pass or fail (the gates do). Loading imports `tool.py` by path under a unique module name,
so two runs in one process never share state and the tool's file name never collides with a
library. The tool's `unittest` tests run with the session available as a module-level `SESSION`
on the test module (set before the module's own code runs and again after, so a placeholder
`SESSION = None` in the file cannot hide it). The schema check derives a sample input and tests
that `validate_arguments` accepts it and refuses the same input plus an extra property. Fuzzing
calls `run` with schema-derived inputs under a per-call bound and counts the clean raises.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by `runner.main`. Calls into `runner.inputs` (the pure derivations),
    `hivemind.llm.ladders.extraction.validate_arguments` (the Hive's own argument check) and the
    standard library's `unittest` and `importlib`.

Key invariants:
    - The tool's code is imported and run only here, after the audit hook is installed.
    - Output the tool prints is counted and discarded, never forwarded.
    - A failure is described by a bounded line (class name and message), never a full traceback.
    - The wall deadline (`DeadlineExceededError`) always propagates.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the phases.
    - hivemind.royal_jelly.quarantine_comb.runner.report for the sections returned.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import importlib.util
import inspect
import io
import json
import sys
import types
import unittest
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from pydantic import JsonValue

from hivemind.llm.ladders.extraction import validate_arguments
from hivemind.royal_jelly.quarantine_comb.runner.inputs import sample_input
from hivemind.royal_jelly.quarantine_comb.runner.report import (
    MAX_LIST_ITEMS,
    MAX_TAIL_CHARS,
    MAX_TEXT_CHARS,
    FuzzSection,
    SchemaSection,
    UnittestSection,
)
from hivemind.royal_jelly.quarantine_comb.runner.session import RecordingSession
from hivemind.royal_jelly.quarantine_comb.runner.usage import CountingWriter, DeadlineExceededError
from hivemind.royal_jelly.spec import TESTS_MODULE, TOOL_MODULE

EXTRA_PROPERTY = "comb_extra_property"  # A name no schema declares: the schema must refuse it.
_FAILURE_LINE_CHARS = 300  # One failed test's description in the report.

RunFunction = Callable[[RecordingSession, dict[str, JsonValue]], Awaitable[object]]

__all__ = [
    "EXTRA_PROPERTY",
    "ToolLoadError",
    "check_schema",
    "load_tool",
    "run_fuzz",
    "run_unittests",
]


class ToolLoadError(Exception):
    """Raise when a module of the package cannot be imported; the message is a bounded excerpt."""


def load_module(
    path: Path, prefix: str, preset: Mapping[str, object] | None = None
) -> types.ModuleType:
    """Import the file at `path` under a unique name, optionally with names set beforehand.

    Args:
        path: The file to import.
        prefix: Start of the unique module name.
        preset: Attributes placed on the module before its own code runs.

    Returns:
        The imported module.

    Raises:
        ToolLoadError: The file could not be imported; the message names the exception.
    """
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ToolLoadError(f"{path.name} cannot be loaded as a module")
    module = importlib.util.module_from_spec(spec)
    for key, value in (preset or {}).items():
        setattr(module, key, value)
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # SAFETY: the tool's import may raise anything.
        raise ToolLoadError(f"{path.name} failed to import: {_describe(error)}") from error
    return module


def load_tool(package_dir: Path) -> types.ModuleType:
    """Import `tool.py` from the package directory."""
    return load_module(package_dir / TOOL_MODULE, "comb_tool")


def run_unittests(
    package_dir: Path, session: RecordingSession, out: CountingWriter
) -> UnittestSection:
    """Run `test_tool.py` with `unittest`, giving the module `SESSION`.

    Args:
        package_dir: Where the tool's files are.
        session: The recording session the tests (and the tool they call) act through.
        out: Counts what the tests print.

    Returns:
        How many tests ran, a line per failure and the tail of unittest's own report.
    """
    # The package directory is importable so the test's `from tool import run` finds tool.py.
    sys.path.insert(0, str(package_dir))
    try:
        module = load_module(package_dir / TESTS_MODULE, "comb_tests", {"SESSION": session})
    except ToolLoadError as error:
        return UnittestSection(ran=0, failures=(), error=str(error)[:MAX_TEXT_CHARS])
    # Set again after the module's code ran: a file's own `SESSION = None` default must not win.
    module.SESSION = session  # type: ignore[attr-defined]  # Attribute added to a loaded module.
    suite = unittest.TestLoader().loadTestsFromModule(module)
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=1)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        result = runner.run(suite)
    problems = [*result.failures, *result.errors]
    failures = tuple(
        f"{test.id()}: {_last_line(trace)}"[:_FAILURE_LINE_CHARS] for test, trace in problems
    )[:MAX_LIST_ITEMS]
    return UnittestSection(
        ran=result.testsRun,
        failures=failures,
        output_tail=stream.getvalue()[-MAX_TAIL_CHARS:],
    )


def check_schema(schema_text: str) -> SchemaSection:
    """Compile the schema, validate a sample derived from it and an extended copy.

    Args:
        schema_text: The spec's `input_schema_json`.

    Returns:
        Whether it compiled, whether the sample was accepted and whether the extra was refused.
    """
    try:
        schema = json.loads(schema_text)
    except ValueError as error:
        return SchemaSection(
            compiled=False, sample_accepted=False, extra_refused=False, findings=(str(error),)
        )
    if not isinstance(schema, dict) or schema.get("type") != "object":
        message = "the top level of the schema must be an object schema (type: object)"
        return SchemaSection(
            compiled=False, sample_accepted=False, extra_refused=False, findings=(message,)
        )
    sample = sample_input(schema)
    problems = validate_arguments(schema, sample)
    # The same sample plus a property no schema declares: a closed schema must say no.
    extra = validate_arguments(schema, {**sample, EXTRA_PROPERTY: 1})
    return SchemaSection(
        compiled=True,
        sample_accepted=not problems,
        extra_refused=bool(extra),
        findings=tuple(problems)[:MAX_LIST_ITEMS],
    )


async def run_fuzz(
    run: RunFunction,
    session: RecordingSession,
    inputs: list[dict[str, JsonValue]],
    timeout_s: float,
) -> FuzzSection:
    """Call `run` once per input, each under `timeout_s`, and judge each outcome.

    Args:
        run: The tool's entry point.
        session: The recording session passed as its first argument.
        inputs: Schema-derived arguments, one call each.
        timeout_s: Wall seconds one call may take.

    Returns:
        How many cases ran, which exception classes were raised cleanly and, if a case broke
        the contract, why (the first such case ends the run).
    """
    if not inspect.iscoroutinefunction(run):
        return FuzzSection(cases=0, clean_raises={}, failure="run is not an `async def` function")
    raises: dict[str, int] = {}
    for index, arguments in enumerate(inputs):
        failure = await _one_case(run, session, copy.deepcopy(arguments), timeout_s, raises)
        if failure:
            return FuzzSection(
                cases=index + 1, clean_raises=raises, failure=f"case {index}: {failure}"
            )
    return FuzzSection(cases=len(inputs), clean_raises=raises, failure="")


async def _one_case(
    run: RunFunction,
    session: RecordingSession,
    arguments: dict[str, JsonValue],
    timeout_s: float,
    raises: dict[str, int],
) -> str:
    """Make one call; return why it broke the contract, or an empty string if it held."""
    try:
        # An external await with a bound: the tool's own code, cut at `timeout_s` (cancelled).
        async with asyncio.timeout(timeout_s) as scope:
            result = await run(session, arguments)
    except DeadlineExceededError:
        raise
    except Exception as error:  # SAFETY: any ordinary exception is a clean raise.
        if scope.expired():
            return f"timed out after {timeout_s:g}s"
        raises[type(error).__name__] = raises.get(type(error).__name__, 0) + 1
        return ""
    except BaseException as error:  # SAFETY: a BaseException is never clean.
        return f"raised {type(error).__name__}, which is not an ordinary exception"
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as error:
        return f"returned a value that is not JSON-serialisable: {_describe(error)}"
    return ""


def _describe(error: BaseException) -> str:
    """Name an exception by class and a bounded message, never its traceback."""
    return f"{type(error).__name__}: {str(error)[:_FAILURE_LINE_CHARS]}"


def _last_line(trace: str) -> str:
    """Return the last non-empty line of a traceback: the exception itself."""
    lines = [line for line in trace.splitlines() if line.strip()]
    return lines[-1].strip() if lines else "failed"
