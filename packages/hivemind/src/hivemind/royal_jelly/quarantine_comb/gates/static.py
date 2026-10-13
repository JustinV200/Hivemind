"""Provide the static gate: the first, pure check of a tool package, by reading its source.

The static gate runs before anything executes. It checks the package is exactly the three files a
tool is, that `spec.json` is the spec the report will name, that no dependency is declared (the
sandbox has no network to install one), that the entry point is `async def run(session,
arguments)` in `tool.py`, that `test_tool.py` holds at least one `unittest` test, and, through
`gates.scan`, that neither module imports or calls anything the banned lists forbid. It reports
the first problem as the reason and every problem as the detail, so one round shows a model
everything to fix. It is cheap and certain; what it cannot see (dynamic behaviour) is the job of
the gates that run the tool.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    First gate in `default_gates()`, run by `QuarantineComb` before the sandbox is touched. Calls
    into `gates.scan`, `hivemind.royal_jelly.spec` and the standard library.

Key invariants:
    - Pure: it reads `ctx.files` and `ctx.spec` only, never the sandbox, never the clock.
    - It never imports or runs the package.
    - A passing static gate means the package has the right shape, not that it is safe.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the gate's place in the order.
    - hivemind.royal_jelly.quarantine_comb.gates.scan for the AST walk and banned.py for the lists.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

from pydantic import ValidationError

from hivemind.royal_jelly.quarantine_comb.gates.base import GateContext, fail, ok
from hivemind.royal_jelly.quarantine_comb.gates.scan import scan_source
from hivemind.royal_jelly.spec import (
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    GateResult,
    ToolSpec,
    spec_sha256,
)

ENTRY_POINT = "tool:run"  # The one entry point a package has: module `tool`, attribute `run`.
_EXPECTED_FILES = (TOOL_MODULE, TESTS_MODULE, SPEC_FILE)  # Exactly these three, nothing else.
_RUN_PARAMETERS = 2  # `run(session, arguments)`.
_TEST_BASES = ("TestCase", "IsolatedAsyncioTestCase")  # Classes whose subclasses unittest runs.

__all__ = ["ENTRY_POINT", "StaticGate"]


class StaticGate:
    """Check a package's shape and scan its source, without running anything."""

    name = "static"
    uses_runner = False

    async def run(self, ctx: GateContext) -> GateResult:
        """Judge the package by reading it.

        Args:
            ctx: The run so far; only `spec` and `files` are read.

        Returns:
            A passing result, or a failing one whose reason is the first problem found.
        """
        problems = [*_file_problems(ctx.files), *_spec_problems(ctx)]
        # Source is only read once the file set is right, or a missing file would cascade.
        if not problems:
            problems.extend(_source_problems(ctx.files))
        if problems:
            return fail(self.name, problems[0], "\n".join(problems))
        return ok(self.name)


def _file_problems(files: Mapping[str, bytes]) -> list[str]:
    """Name every file that is missing from, or extra to, the three a tool is."""
    problems = [f"missing file '{name}'" for name in _EXPECTED_FILES if name not in files]
    problems.extend(
        f"unexpected file '{name[:80]}': a package holds only {', '.join(_EXPECTED_FILES)}"
        for name in files
        if name not in _EXPECTED_FILES
    )
    return problems


def _spec_problems(ctx: GateContext) -> list[str]:
    """Check `spec.json` against the spec, then the spec's dependencies and entry point."""
    problems: list[str] = []
    raw = ctx.files.get(SPEC_FILE)
    if raw is not None:
        try:
            packaged = ToolSpec.from_json(raw.decode("utf-8"))
        except (ValidationError, UnicodeDecodeError):
            problems.append("spec.json is not a valid ToolSpec")
        else:
            if spec_sha256(packaged) != spec_sha256(ctx.spec):
                problems.append(
                    "spec.json does not match the spec this run is for (sha256 differs)"
                )
    if ctx.spec.dependencies:
        names = ", ".join(ctx.spec.dependencies)[:120]
        problems.append(f"declares dependencies ({names}): the sandbox cannot install dependencies")
    if ctx.spec.entry_point != ENTRY_POINT:
        problems.append(f"entry point {ctx.spec.entry_point!r} must be {ENTRY_POINT!r}")
    return problems


def _source_problems(files: Mapping[str, bytes]) -> list[str]:
    """Scan both modules and check the entry point and the tests exist."""
    problems: list[str] = []
    trees: dict[str, ast.Module] = {}
    for name in (TOOL_MODULE, TESTS_MODULE):
        try:
            source = files[name].decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{name} is not valid UTF-8 text")
            continue
        findings = scan_source(name, source)
        problems.extend(found.text() for found in findings)
        if not findings:
            trees[name] = ast.parse(source)
    if TOOL_MODULE in trees and not _defines_run(trees[TOOL_MODULE]):
        problems.append("tool.py must define `async def run(session, arguments)` at module level")
    if TESTS_MODULE in trees and not _defines_a_test(trees[TESTS_MODULE]):
        problems.append(
            "test_tool.py must define at least one unittest.TestCase with a test method"
        )
    return problems


def _defines_run(tree: ast.Module) -> bool:
    """Return whether the module defines `async def run` taking exactly two parameters."""
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            arguments = node.args
            positional = len(arguments.posonlyargs) + len(arguments.args)
            return positional == _RUN_PARAMETERS and arguments.vararg is None
    return False


def _defines_a_test(tree: ast.Module) -> bool:
    """Return whether some class derives a unittest test case and has a `test*` method."""
    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and _is_test_case(node)
            and any(
                isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and member.name.startswith("test")
                for member in node.body
            )
        ):
            return True
    return False


def _is_test_case(node: ast.ClassDef) -> bool:
    """Return whether any base reads as `TestCase` or `unittest.IsolatedAsyncioTestCase`."""
    return any(ast.unparse(base).split(".")[-1] in _TEST_BASES for base in node.bases)
