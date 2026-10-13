"""Share what every `hive tools` command needs: its `--manifest`, the database, and how errors exit.

`hive tools` is the operator's door into the Comb Registry (the catalogue of promoted Royal Jelly
tools) and the Quarantine Comb (the sandbox every new tool must pass). Codingrules 5.1 caps a
function at five parameters and typer turns each parameter into one option, so `--manifest` belongs
to the group, as it does for `hive honey`: it comes right after `tools` and before the subcommand
(`hive tools --manifest hive.toml list`). The callback only keeps the path; `cli_context` loads the
manifest once a command body runs, so a subcommand's `--help` never needs one, and `quarantine`
can run without any manifest at all. `run_or_exit` is the one place a typed Hive error becomes a
stderr line and an exit code.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `tools` command group. Imported by
    `hivemind.cli.tools.listing`, `.lifecycle`, `.request` and `.quarantine`. Calls into
    `hivemind.cli.stores`, `hivemind.manifest`, `hivemind.common` and waggle only.

Key invariants:
    - Every error a command body reports is a typed `HiveMindError` (exit 1) or an input the
      operator got wrong (exit 2); no command prints a traceback for either.
    - The operator's writes are recorded with the actor `human`, the trail's own literal for the
      person (`HUMAN_ACTOR`).
    - A tool is HIVE scope unless `--cell` names a Cell, which makes it CELL scope: one option
      says both, so the two can never disagree.

See Also:
    - hivemind.cli.honey.context for the group-callback pattern this follows.
    - hivemind.royal_jelly.registry for ToolKey, the identity `tool_key` builds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cli.stores import DEFAULT_MANIFEST, load_manifest_or_exit
from hivemind.common.errors import HiveMindError
from hivemind.manifest import HiveManifest
from hivemind.royal_jelly.registry import ToolKey
from waggle.errors import InvalidIdError
from waggle.ids import CellId, IdKind, parse_id
from waggle.messages.tool.authoring import ToolScope

HUMAN_ACTOR = "human"  # Every write these commands record is the operator's own act.
EXIT_REFUSED = 1  # The Hive refused or could not do what was asked.
EXIT_BAD_INPUT = 2  # The command line itself was wrong (a name, a path, a Cell id).

CellOption = Annotated[
    str | None,
    typer.Option(
        "--cell", help="The Cell a CELL-scope tool belongs to; without it the tool is HIVE scope."
    ),
]

__all__ = [
    "EXIT_BAD_INPUT",
    "EXIT_REFUSED",
    "HUMAN_ACTOR",
    "CellOption",
    "ToolsCliContext",
    "cell_id_of",
    "cli_context",
    "input_or_exit",
    "manifest_or_none",
    "parse_cell_id",
    "run_or_exit",
    "tool_key",
    "tools_callback",
]

_MANIFEST_HELP = (
    f"The Hive Manifest TOML file (default: {DEFAULT_MANIFEST}); `quarantine` runs on default "
    "limits when none is given."
)


@dataclass(frozen=True, slots=True)
class ToolsCliContext:
    """The manifest and database every `hive tools` command shares."""

    manifest: HiveManifest  # The loaded Hive Manifest.
    db: Path  # The SQLite file holding this Hive's Comb Registry and trail.


@dataclass(frozen=True, slots=True)
class _Options:
    """The group's own option, as typed, before any file is read."""

    manifest: Path | None  # `--manifest`, when given.


def tools_callback(
    ctx: typer.Context,
    manifest: Annotated[Path | None, typer.Option("--manifest", help=_MANIFEST_HELP)] = None,
) -> None:
    """Keep `--manifest` for whichever `hive tools` subcommand runs."""
    # Only kept here, loaded by `cli_context` when a command body runs: `hive tools list --help`
    # must answer on a machine with no manifest to hand, and a callback that loaded it could not.
    ctx.obj = _Options(manifest=manifest)


def cli_context(ctx: typer.Context) -> ToolsCliContext:
    """Load the manifest the group's option names, and resolve the database file.

    Args:
        ctx: The subcommand's own typer context; its parent ran `tools_callback`.

    Returns:
        The shared manifest and database.

    Raises:
        typer.Exit: Code 2, through `load_manifest_or_exit`, when the manifest is missing or
            fails validation.
    """
    options: _Options = ctx.obj
    loaded = load_manifest_or_exit(options.manifest or DEFAULT_MANIFEST)
    return ToolsCliContext(manifest=loaded, db=loaded.resolve_path(loaded.hive.db))


def manifest_or_none(ctx: typer.Context) -> HiveManifest | None:
    """Load the manifest only when `--manifest` was given; None otherwise.

    Args:
        ctx: The subcommand's own typer context.

    Returns:
        The loaded manifest, or None when the operator named none (`quarantine` then runs on the
        default limits).

    Raises:
        typer.Exit: Code 2 when a named manifest is missing or fails validation.
    """
    options: _Options = ctx.obj
    return None if options.manifest is None else load_manifest_or_exit(options.manifest)


def cell_id_of(cell: str | None) -> CellId | None:
    """Check a `--cell` value is a Cell id and return it; None when no Cell was named.

    Args:
        cell: The option's raw text.

    Returns:
        The Cell id, or None.

    Raises:
        typer.Exit: Code 2 when the text is not a well-formed Cell id.
    """
    return None if cell is None else parse_cell_id(cell)


def parse_cell_id(text: str) -> CellId:
    """Check TEXT is a well-formed Cell id and return it.

    Args:
        text: What the operator typed where a Cell id belongs.

    Returns:
        The Cell id.

    Raises:
        typer.Exit: Code 2 when the text is not a well-formed Cell id.
    """
    try:
        return CellId(parse_id(text, IdKind.CELL))
    except InvalidIdError as exc:
        typer.echo(f"{text!r} is not a Cell id: {exc}", err=True)
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc


def tool_key(name: str, cell: str | None) -> ToolKey:
    """Build the registry key for NAME: CELL scope when `--cell` names a Cell, else HIVE.

    Args:
        name: The tool's name.
        cell: The `--cell` option's raw text, or None.

    Returns:
        The key every registry read and write takes.

    Raises:
        typer.Exit: Code 2 when `cell` is not a Cell id.
    """
    cell_id = cell_id_of(cell)
    return ToolKey(name, ToolScope.HIVE if cell_id is None else ToolScope.CELL, cell_id)


def input_or_exit[ResultT](build: Callable[[], ResultT]) -> ResultT:
    """Run a synchronous builder, turning input it rejects into a stderr line and exit 2.

    Args:
        build: Builds a value from what the operator typed (a request, a spec, a limits table).

    Returns:
        Whatever `build` returned.

    Raises:
        typer.Exit: EXIT_BAD_INPUT when `build` raised a ValueError (pydantic's ValidationError
            is one), so a bad name or description never prints a traceback.
    """
    try:
        return build()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc


def run_or_exit[ResultT](work: Coroutine[object, None, ResultT]) -> ResultT:
    """Run one command's async work, turning a typed Hive error into a clean stderr line.

    Args:
        work: The command's coroutine.

    Returns:
        Whatever `work` returned.

    Raises:
        typer.Exit: EXIT_REFUSED for a HiveMindError, printed as `code: message`; EXIT_BAD_INPUT
            for a ValueError (a reason the registry would not take, a malformed id).
    """
    try:
        return asyncio.run(work)
    except HiveMindError as exc:
        # A typed refusal (not found, a promotion rule, a wrong principal): one line with its
        # stable code, never a traceback.
        typer.echo(f"{exc.code}: {exc}", err=True)
        raise typer.Exit(code=EXIT_REFUSED) from exc
    except ValueError as exc:
        # The operator's own input past what typer can check (an empty or oversized reason).
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc
