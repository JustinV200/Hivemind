"""Build the `hive` CLI's typer application: the CLI's composition root.

This is the composition root (codingrules section 8.2: "the composition root is exactly one
place per entry point") for the `hive` command-line interface. It builds the `typer.Typer`
application, registers each command group, and exposes `main()` as the console-script entry
point declared in `packages/hivemind/pyproject.toml`. Only wiring lives here: every command group
is a thin typer layer that calls straight into a subsystem's public API (codingrules section 2's
CLI row), never containing logic of its own.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script. Calls into hivemind.cli.version, hivemind.cli.tasks, hivemind.cli.trail,
    hivemind.cli.llm, hivemind.cli.capping, hivemind.cli.run and hivemind.cli.readback (cells,
    inbox, wardens) now; later phases add entrance and friends through their own public APIs.

Key invariants:
    - `hive --version` and a bare `hive` both exit 0.
    - Every command group registered below is a thin typer layer with no logic of its own
      (codingrules section 2's CLI row); this file only wires them together.
    - `hive run` is registered with `app.command("run")`, not `app.add_typer`: unlike every other
      group here, it has no subcommand of its own (`hive run "goal"`, not `hive run run "goal"`),
      and this repository's pinned typer version does not collapse a single-command `add_typer`
      sub-app to its parent's own command name (verified empirically; see `hivemind.cli.run`'s own
      module docstring).

See Also:
    - .claude/codingrules.md section 8.2 for the composition-root rule this file follows.
    - .claude/codingrules.md section 5.6 for the module-count limit hivemind.cli.readback exists
      to keep this package under.
    - hivemind.cli.version for what `--version` prints.
    - hivemind.cli.tasks and hivemind.cli.trail for the two command groups roadmap step 2.9 adds.
    - hivemind.cli.llm and hivemind.cli.capping for the two command groups roadmap step 3.21's
      first half adds; hivemind.cli.stores for the manifest-aware composition helpers both use.
    - hivemind.cli.run and hivemind.cli.readback for the four commands roadmap step 3.21's second
      half adds, and hivemind.cli.compose for the composition root they are built on.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli import capping, llm, tasks, trail
from hivemind.cli.readback import cells_app, inbox_app, wardens_app
from hivemind.cli.run import run_command
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

# Roadmap step 3.21 (first half): inspect model providers/slots and the Capping proposal queue.
# Neither depends on a running Queen -- `llm` builds its own ProviderRegistry from a manifest, and
# `capping` reads proposal state back out of the trail -- so both land ahead of `hive run`.
app.add_typer(llm.app, name="llm")
app.add_typer(capping.app, name="capping")

# Roadmap step 3.21 (second half): compose and run a Hive, and inspect it while it runs.
# `run` is a bare command, not a group (see this module's own "Key invariants" and
# hivemind.cli.run's own docstring for why `app.command` and not `app.add_typer` here); `cells`,
# `inbox` and `wardens` (hivemind.cli.readback) are ordinary single- or multi-subcommand groups.
app.command("run")(run_command)
app.add_typer(cells_app, name="cells")
app.add_typer(inbox_app, name="inbox")
app.add_typer(wardens_app, name="wardens")

# ──────────────────────────────────────────────────────────────────────────────
# Command groups added by later roadmap steps. Each is `app.add_typer(<group>.app, name=...)`,
# registered here so this file stays the single place that assembles the CLI:
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
