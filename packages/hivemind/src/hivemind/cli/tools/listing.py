"""Provide `hive tools list` and `hive tools show`: read the Comb Registry, change nothing.

The Comb Registry (the catalogue of promoted Royal Jelly tools, ADR-0046) holds every version of
every tool, current or retired. `list [--scope hive|cell] [--cell ID] [--all] [--json]` prints one
row per tool's current version -- name, version, scope, Cell, state, short package hash, promoted
at -- or, with `--all`, every stored version, retired ones included. `show NAME [--cell ID]
[--version V] [--json]` prints one version in full: its spec (description, input schema,
capabilities, supported operating systems), the Quarantine Comb report's summary (path, gates
passed, the failing gate), and the package's hash and size. Naming no version means the current
one; `--cell` names a Cell and so makes the tool CELL scope.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `tools` command group. Mounted by
    `hivemind.cli.tools` as `list` and `show`. Calls into `hivemind.cli.stores` (the registry),
    `hivemind.royal_jelly` and this group's own `context` and `render` only.

Key invariants:
    - Read-only: nothing here writes the registry or the trail, and nothing runs a tool.
    - A name with no such version, or no current version, exits 1 with the registry's code.

See Also:
    - hivemind.royal_jelly.registry for CombRegistry, what these commands read.
    - hivemind.cli.tools.lifecycle for the commands that change a tool's standing.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli.stores import JsonOption, open_comb_registry
from hivemind.cli.tools.context import CellOption, cell_id_of, cli_context, run_or_exit, tool_key
from hivemind.cli.tools.render import print_row_detail, print_rows, row_detail_json, rows_json
from waggle.messages.tool.authoring import ToolScope

__all__ = ["list_command", "show_command"]

ScopeOption = Annotated[
    ToolScope | None,
    typer.Option("--scope", case_sensitive=False, help="Only tools at this scope (hive or cell)."),
]
AllOption = Annotated[
    bool, typer.Option("--all", help="Every stored version, retired ones included.")
]
VersionOption = Annotated[
    str | None, typer.Option("--version", help="The version to show; the current one if omitted.")
]


def list_command(
    ctx: typer.Context,
    scope: ScopeOption = None,
    cell: CellOption = None,
    all_versions: AllOption = False,
    as_json: JsonOption = False,
) -> None:
    """List the Comb Registry's tools: each one's current version, or every version with --all."""
    cli_ctx = cli_context(ctx)
    registry = open_comb_registry(cli_ctx.db)
    # Local SQLite, one indexed read: the registry orders rows by name, scope, Cell and version.
    rows = run_or_exit(registry.list_tools(scope, cell_id_of(cell), current_only=not all_versions))
    if as_json:
        typer.echo(rows_json(rows))
        return
    print_rows(rows)


def show_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="The tool's name.")],
    cell: CellOption = None,
    version: VersionOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show one tool version in full: spec, report summary, package hash and size."""
    cli_ctx = cli_context(ctx)
    key = tool_key(name, cell)
    registry = open_comb_registry(cli_ctx.db)
    # No version named means the current one; a tool with none (never promoted, or retired
    # without a replacement) is ToolNotFoundError, printed with its code.
    row = run_or_exit(registry.get(key.name, key.scope, key.cell_id, version))
    if as_json:
        typer.echo(row_detail_json(row))
        return
    print_row_detail(row)
