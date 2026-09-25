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
    hivemind.cli.llm, hivemind.cli.capping, hivemind.cli.run, hivemind.cli.memory,
    hivemind.cli.forage, hivemind.cli.readback (cells, inbox, wardens, cluster) and
    hivemind.cli.recordings, hivemind.cli.honey and hivemind.cli.serve (`hive serve`, roadmap
    step 10.5) now; later phases add entrance and friends through their own public APIs.

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
    - hivemind.cli.recordings for `hive recordings`, the flight recorder's reader (step 6.6).
"""

from __future__ import annotations

import os
import sys
from typing import Annotated

import typer

from hivemind.cli import capping, forage, honey, llm, memory, recordings, tasks, trail
from hivemind.cli.entrance import app as entrance_app
from hivemind.cli.keys import app as keys_app
from hivemind.cli.readback import cells_app, cluster_app, inbox_app, wake_command, wardens_app
from hivemind.cli.remote import app as remote_app
from hivemind.cli.run import RunCommand, run_command
from hivemind.cli.serve import serve_command
from hivemind.cli.version import collect_version_info, format_version
from hivemind.common.logging import configure_logging
from hivemind.manifest.env import read_env

__all__ = ["app", "main"]

# When HIVEMIND_LOG_LEVEL is unset; lines go to standard error. An operator's terminal shows
# trouble, not every heartbeat.
_DEFAULT_LOG_LEVEL = "WARNING"

# The one Typer application every command group below attaches to. Building it at module level
# is the composition root itself doing its job (codingrules 5.5 bars *side effects* on import,
# not the object construction a composition root exists to perform).

app = typer.Typer(
    name="hive",
    help="Command the Hive: the Queen (the central orchestrator) and everything it runs.",
    # invoke_without_command lets `hive --version` and a bare `hive` both reach the callback
    # below instead of typer demanding a subcommand first.
    invoke_without_command=True,
    # Help text is plain: Rich markup read `[hive]`, `[entrance]` and `[llm.providers]`, the
    # manifest sections the help names, as style tags and printed nothing in their place.
    rich_markup_mode=None,
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
app.command("run", cls=RunCommand)(run_command)
app.add_typer(cells_app, name="cells")
app.add_typer(inbox_app, name="inbox")
app.add_typer(wardens_app, name="wardens")

# Roadmap step 4.11: memory (hot state, pins, compaction, Cell Wax), forage (the ledger and grant
# revisions), and Clustering's own operator orders (`hive cluster`/`hive wake`, docs/adr/0024).
# `cluster` lives in `hivemind.cli.readback` (not a flat `hivemind.cli.cluster` module) because it
# is mostly a read reconstructed from durable stores plus one small write, the same shape
# `readback.inbox`'s own `answer` command already has, and `hivemind.cli` itself is already at
# codingrules section 5.6's ten-module limit without it; `wake` is a bare root command (`hive
# wake`, not `hive cluster wake`), matching `hive run`'s own shape.
app.add_typer(memory.app, name="memory")
app.add_typer(forage.app, name="forage")
app.add_typer(cluster_app, name="cluster")
app.command("wake")(wake_command)

# Roadmap step 6.6: read the flight recorder's recordings back out of the Hive's database (list,
# show a pixel-free summary, export the self-contained playback page); like `llm`, it needs no
# running Queen.
app.add_typer(recordings.app, name="recordings")
# Roadmap steps 7.10 and 7.11: query, count, ripen, re-embed, browse and relabel the Honey Store.
app.add_typer(honey.app, name="honey")
# Roadmap step 10.5: run the Queen with the Hive Entrance (the Hive's one HTTP door) until
# interrupted. A bare command like `run` (`hive serve`, not `hive serve serve`).
app.command("serve")(serve_command)

# Roadmap step 10.8: keep the Hive Entrance from the Hive Stand (the operator password, devices,
# the door), each decision made as the console device over a running serve's loopback listener.
app.add_typer(entrance_app, name="entrance")
# Roadmap step 10.8: this device's side of a remote Hive (`hive remote enrol`; `hive run --remote`
# and `hive inbox --remote` are options of the commands above), and the Hive's Waggle-side keys.
app.add_typer(remote_app, name="remote")
app.add_typer(keys_app, name="keys")

# ──────────────────────────────────────────────────────────────────────────────
# Command groups added by later roadmap steps. Each is `app.add_typer(<group>.app, name=...)`,
# registered here so this file stays the single place that assembles the CLI:
#   doctor     - environment and manifest diagnostics
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
    # Once, before any subcommand builds a Hive: a command's standard output is its own report
    # (`hive run --json` is JSON and nothing else), so logs go to standard error. Here rather
    # than in `main` so every invocation configures it, a test runner's included.
    _configure_logging()
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
    _tolerate_console_encoding()
    app()


def _configure_logging() -> None:
    """Send every log line to standard error, at `HIVEMIND_LOG_LEVEL` (WARNING by default).

    Unconfigured, structlog prints every level, debug included, to standard output, where it
    corrupted `hive run --json` and interleaved with every table (a real `hive run` printed the
    Entrance's debug lines between its progress lines). Production (`HIVEMIND_ENV=prod`) logs JSON
    lines for an aggregator; anywhere else a person reads them.
    """
    env = read_env(os.environ)
    level = env.log_level or _DEFAULT_LOG_LEVEL
    configure_logging(json_output=env.env == "prod", level=level)


def _tolerate_console_encoding() -> None:
    """Never let a task summary crash the CLI on a console that cannot encode it.

    A Windows console (or a redirected stdout) defaults to a legacy code page such as cp1252; a
    Drone's own summary can carry any Unicode (a real run printed a check-mark emoji and died in
    `hive run`'s final report). Output is what the CLI is for, so an unencodable character is
    replaced rather than fatal; the trail and the Brood Chamber still hold the exact text.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
