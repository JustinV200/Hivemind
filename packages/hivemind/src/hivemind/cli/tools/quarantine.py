"""Provide `hive tools quarantine PATH`: run the Quarantine Comb over a package in the dev sandbox.

The operator's dev loop for writing a tool. PATH is a directory holding `tool.py`, `test_tool.py`
and `spec.json` (packed here into the deterministic package format, so the Comb sees exactly what
the registry would store) or a `.zip` package already made. The command reads the `ToolSpec` from
`spec.json`, builds the Comb and a `SandboxSubprocess`, runs all six gates in order with
`[royal_jelly.limits]` from `--manifest` (or the Comb's defaults with none; `--limits-json
'{"wall_s": 30}'` overrides single limits on top), prints each gate with pass or fail and its
reason, then what the run spent, and exits 0 on a pass or 1 on a fail.

It always uses the subprocess sandbox, a temporary directory and a scrubbed environment on this
machine, with no isolation worth the name (ADR-0047): the command says so in one line, every time.
A pass here proves the tool for the dev loop and nothing more; promotion always runs the Comb in a
sandbox Cell, and nothing here touches the registry, the Basket or the trail.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `tools` command group. Mounted by
    `hivemind.cli.tools` as `quarantine`. Calls into `hivemind.royal_jelly` (the Comb, its
    sandbox, the package format and the limits) and this group's own `context` and `render` only.

Key invariants:
    - Read-only on the Hive: no database is opened and no event is recorded.
    - The package under test is untrusted; it runs only inside `SandboxSubprocess`, never in this
      process, and the sandbox is cleaned up whether the run passes, fails or raises.
    - A package this command cannot read (missing files, bad zip, bad spec) exits 2 with the
      reason; it never reaches the Comb.

See Also:
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for the gates and the sandbox.
    - hivemind.royal_jelly.quarantine_comb for QuarantineComb.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from hivemind.cli.tools.context import (
    EXIT_BAD_INPUT,
    EXIT_REFUSED,
    input_or_exit,
    manifest_or_none,
    run_or_exit,
)
from hivemind.cli.tools.render import print_report
from hivemind.manifest import HiveManifest
from hivemind.royal_jelly import (
    SPEC_FILE,
    PackageError,
    QuarantineComb,
    QuarantineLimits,
    QuarantineReport,
    SandboxSubprocess,
    ToolSpec,
    pack,
    unpack,
)
from hivemind.royal_jelly.access import limits_from_section
from waggle.clock import SystemClock
from waggle.messages.tool.authoring import QuarantinePath

__all__ = ["quarantine_command"]

SANDBOX_NOTE = (
    "note: running in the development subprocess sandbox on this machine, with no isolation; "
    "promotion always runs the Comb in a sandbox Cell."
)
_ZIP_SUFFIX = ".zip"
_SKIPPED_PARTS = frozenset({"__pycache__"})  # Interpreter droppings are never part of a tool.


def quarantine_command(
    ctx: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(help="A directory with tool.py, test_tool.py and spec.json, or a .zip."),
    ],
    limits_json: Annotated[
        str | None,
        typer.Option(
            "--limits-json", help="JSON object of limits to override, e.g. '{\"wall_s\": 30}'."
        ),
    ] = None,
) -> None:
    """Run the Quarantine Comb over PATH in the development subprocess sandbox; exit 1 on a fail."""
    manifest = manifest_or_none(ctx)
    package, spec = _load_package(path)
    limits = input_or_exit(lambda: _limits(manifest, limits_json))
    typer.echo(SANDBOX_NOTE)
    report = run_or_exit(_run(package, spec, limits))
    print_report(report)
    # The exit code is the verdict, so a script (or a test runner) can gate on it.
    if not report.passed:
        raise typer.Exit(code=EXIT_REFUSED)


async def _run(package: bytes, spec: ToolSpec, limits: QuarantineLimits) -> QuarantineReport:
    """Run the Comb in a fresh subprocess sandbox; the Comb cleans the sandbox up itself."""
    clock = SystemClock()
    sandbox = SandboxSubprocess(clock, output_bytes=limits.output_bytes)
    comb = QuarantineComb(clock)
    try:
        # External awaits inside: the runner is a child process, bounded by the wall limit plus
        # a grace period and killed as a process group when it overruns.
        return await comb.run(package, spec, sandbox, limits, QuarantinePath.SANDBOX_CELL)
    finally:
        # The Comb cleans up on every path it controls; this covers an error before it starts.
        await sandbox.cleanup()


def _limits(manifest: HiveManifest | None, limits_json: str | None) -> QuarantineLimits:
    """Return the manifest's limits (the Comb's defaults without one), with overrides on top."""
    base = (
        QuarantineLimits() if manifest is None else limits_from_section(manifest.royal_jelly.limits)
    )
    if limits_json is None:
        return base
    overrides = json.loads(limits_json)
    if not isinstance(overrides, dict):
        raise ValueError("--limits-json must be a JSON object of limits to override.")
    # Validated as a whole, so an override outside a limit's bounds is refused like a manifest's.
    return QuarantineLimits.model_validate({**base.model_dump(), **overrides})


def _load_package(path: Path) -> tuple[bytes, ToolSpec]:
    """Read PATH into package bytes and the spec in its `spec.json`, or exit 2 saying why."""
    try:
        package = _package_bytes(path)
        files = unpack(package)
        if SPEC_FILE not in files:
            raise ValueError(f"{path} holds no {SPEC_FILE}; the package needs its ToolSpec.")
        return package, ToolSpec.from_json(files[SPEC_FILE].decode("utf-8"))
    except (PackageError, ValidationError, ValueError, OSError) as exc:
        # A package that cannot be read never reaches the Comb: it is the operator's input.
        typer.echo(f"cannot read the package at {path}: {exc}", err=True)
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc


def _package_bytes(path: Path) -> bytes:
    """Return a directory packed in the deterministic format, or a `.zip` as it is."""
    if path.is_dir():
        files = {
            file.relative_to(path).as_posix(): file.read_bytes()
            for file in sorted(path.rglob("*"))
            if file.is_file() and not _SKIPPED_PARTS & set(file.relative_to(path).parts)
        }
        return pack(files)
    if path.suffix == _ZIP_SUFFIX and path.is_file():
        return path.read_bytes()
    raise ValueError(f"{path} is neither a directory nor a {_ZIP_SUFFIX} file.")
