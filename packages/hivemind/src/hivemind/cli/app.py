"""Build the `hive` CLI's typer application: the CLI's composition root.

This is the composition root (codingrules section 8.2: "the composition root is exactly one
place per entry point") for the `hive` command-line interface. It builds the `typer.Typer`
application, registers each command group, and exposes `main()` as the console-script entry
point declared in `packages/hivemind/pyproject.toml`. Only wiring lives here: every command group
is a thin typer layer that calls straight into a subsystem's public API (codingrules section 2's
CLI row), never containing logic of its own.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script. Calls into hivemind.cli.version, hivemind.cli.tasks and hivemind.cli.trail
    now; later phases add queen, entrance and friends through their own public APIs.

Key invariants:
    - `hive --version` and a bare `hive` both exit 0.
    - Every command group registered below is a thin typer layer with no logic of its own
      (codingrules section 2's CLI row); this file only wires them together.

See Also:
    - .claude/codingrules.md section 8.2 for the composition-root rule this file follows.
    - hivemind.cli.version for what `--version` prints.
    - hivemind.cli.tasks and hivemind.cli.trail for the two command groups roadmap step 2.9 adds.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli import tasks, trail
from hivemind.cli.version import collect_version_info, format_version

__all__ = ["app", "main"]

# The one Typer application every command group below attaches to. Building it at module level
# is the composition root itself doing its job (codingrules 5.5 bars *side effects* on import,
# not the object construction a composition root exists to perform).
app = typer.Typer(
    name="hive",
    help="Command the Hive: the Queen (the central orchestrator) and everything it runs.",
    # invoke_without_command lets `hive --version` and a bare `hive` both reach the callback
    # below instead of typer demanding a subcommand first.
    invoke_without_command=True,
)

# Roadmap step 2.9: submit and inspect tasks in the Brood Chamber, and read the Pheromone Trail.
app.add_typer(tasks.app, name="tasks")
app.add_typer(trail.app, name="trail")

# ──────────────────────────────────────────────────────────────────────────────
# Command groups added by later roadmap steps. Each is `app.add_typer(<group>.app, name=...)`,
# registered here so this file stays the single place that assembles the CLI:
#   run        - start a Hive from a manifest
#   doctor     - environment and manifest diagnostics
#   cluster    - pause/resume a Hive while a provider is unavailable
#   wake       - trigger an awake episode by hand
#   entrance   - manage the Hive Entrance's listeners and enrolled devices
#   supersede  - move the Hive Stand to a new machine
#   backup     - snapshot the Brood Chamber, Honey Store and Pheromone Trail
#   restore    - restore a Hive from a backup
# ──────────────────────────────────────────────────────────────────────────────


def _print_version(show_version: bool) -> None:
    """Print the `--version` line and exit, if the flag was passed.

    Args:
        show_version: The value of the eager `--version` option. A no-op when False, so it is
            safe to call unconditionally from the root callback.

    Raises:
        typer.Exit: Always, when `show_version` is True, so typer stops before looking for a
            subcommand.
    """
    if not show_version:
        return
    # importlib.metadata + platform reads are cheap and synchronous; no timeout concerns here.
    typer.echo(format_version(collect_version_info()))
    raise typer.Exit(code=0)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Print the hive and Python version and exit.",
            is_eager=True,  # Runs before any subcommand resolution, matching --help's behaviour.
        ),
    ] = False,
) -> None:
    """Run the root `hive` command: handle `--version`, or fall back to help.

    Args:
        ctx: The invocation context typer supplies; used to detect that no subcommand was given.
        version: Set by `--version`. Handled by `_print_version`, which exits before returning.
    """
    _print_version(version)
    # No subcommand exists yet (this step only adds --version), so a bare `hive` prints help
    # instead of typer's default "Missing command" error, and still exits 0.
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


def main() -> None:
    """Run the `hive` CLI. The console-script entry point in `packages/hivemind/pyproject.toml`.

    Example:
        `uv run hive --version` prints one line and exits 0.
    """
    app()
