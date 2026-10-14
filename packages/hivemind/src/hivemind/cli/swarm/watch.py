"""Provide `hive swarm watch`: name the directories a device's idle Warden reads logs and files in.

A Real Cell's Warden (the always-on supervisor of one Cell) with no bees working watches its Cell
(ADR-0054): it always looks at the process list and resource use, and it reads the tail of log
files and the files that changed only inside directories the operator has named. `hive swarm watch
NODE DIRECTORY...` names them for one device, in the device's own spelling (`/var/log/app`, or
`C:/logs` on Windows), replacing the ones named before; `hive swarm watch NODE` alone shows them
and writes nothing; `hive swarm watch NODE --clear` stops the file watching. A directory is
refused, with its position, when it is not an absolute path on the device or the device's access
level does not let the Hive read it. The command writes the Hive's own database, so a Hive that is
running applies it on its next beat (it rebuilds the device's Warden, as it does for a new access
level) and one that is not applies it when it starts. It records `swarm.watch_changed` as the
actor `human`, and a command that finds the device already watching exactly that says so and
writes nothing. NODE is a device id, a Cell id or a unique label.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Mounted by
    `hivemind.cli.swarm` as `watch`. Calls into this group's `context` and `render`,
    `hivemind.cli.tools.context` (the error exits) and `hivemind.swarm.operator` only.

Key invariants:
    - The command contains no decision of its own: it names the device, asks the desk, prints.
    - Naming nothing writes nothing; naming directories together with `--clear` is a usage error.
    - A refusal exits 1 with its stable code and has written and recorded nothing.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for why the operator names them.
    - hivemind.swarm.operator.roots for what a list of directories may be.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

import typer

from hivemind.cli.swarm.context import NodeArgument, cli_context, open_desk
from hivemind.cli.swarm.render import describe
from hivemind.cli.tools.context import run_or_exit
from hivemind.swarm.operator import NodeChange, OperatorDesk

DirectoriesArgument = Annotated[
    list[str] | None,
    typer.Argument(
        help="Absolute directories on the device, spelled as the device spells them (/var/log/app, "
        "C:/logs): the log files and file changes in them are what its Warden reads while it is "
        "idle. They replace the ones named before. Leave them out to show the current ones."
    ),
]
ClearOption = Annotated[
    bool,
    typer.Option("--clear", help="Stop watching any directory: only processes and resource use."),
]

__all__ = ["watch_command"]


def watch_command(
    ctx: typer.Context,
    node: NodeArgument,
    directories: DirectoriesArgument = None,
    clear: ClearOption = False,
) -> None:
    """Name the directories whose logs and file changes a device's idle Warden reads."""
    if directories and clear:
        raise typer.BadParameter("Name directories or give --clear, not both.")
    desk = open_desk(cli_context(ctx))
    if not directories and not clear:
        # Nothing asked for: show what is set, and write nothing.
        found = run_or_exit(desk.find(node))
        typer.echo(_watching(describe(found), found.watch_roots))
        return
    change = run_or_exit(_watch(desk, node, tuple(directories or ())))
    name = describe(change.node)
    if not change.is_changed:
        typer.echo(f"{name} already watches exactly that; nothing changed.")
        return
    typer.echo(
        f"{_watching(name, change.node.watch_roots)} The running Hive rebuilds the device's Warden "
        "on its next beat, as for a new access level, so a task running on it is paused and "
        "resumed."
    )


async def _watch(desk: OperatorDesk, reference: str, directories: tuple[str, ...]) -> NodeChange:
    """Find the device and name the directories its Warden watches."""
    return await desk.set_watch_roots(await desk.find(reference), directories)


def _watching(name: str, roots: Sequence[str]) -> str:
    """Say which directories a device's Warden reads, as one sentence."""
    if not roots:
        return f"{name} watches no directory: its Warden reads processes and resource use only."
    noun = "directory" if len(roots) == 1 else "directories"
    return f"{name} watches {len(roots)} {noun}: {', '.join(roots)}."
