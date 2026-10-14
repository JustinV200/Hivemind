"""Assemble `hive swarm`: invite, list, revoke, access, shield, promote, demote and run.

Roadmap step 11.12 gives the operator a terminal for the Swarm (the mesh of existing machines the
Hive borrows as Real Cells, each carrying a Pollen Packet, the thin gateway installed on it).
The commands are split the way `hivemind.cli.tools` and `hivemind.cli.basket` split theirs:
`invite` (mint a single-use invite), `listing` (`list`), `changes` (`revoke`, `access`, `shield`,
`promote`, `demote`: the operator's acts on a device, each a call to `hivemind.swarm.operator`) and
`run` (a goal that can be placed on one device only, with `pinned` building its ceiling and waiting
for the device), with a shared `context` (the group's `--manifest`, the operator's desk, the
argument types) and `render` (printing). `cells` is not a command: it reads the enrolled devices as
Cells for `hive cells list`. `--manifest` belongs to the group, so it comes right after `swarm` and
before the subcommand (`hive swarm --manifest hive.toml list`). A device is named by its device id,
a Cell id or its label. The commands write the Hive's database; a Hive that is running reads it on
its next beat and does the work, so none of them needs one running (except `run`, which builds its
own). This face only wires them together (codingrules 5.4).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts
    `app` under the `swarm` command-group name. Calls into `hivemind.cli.swarm.context`,
    `.invite`, `.listing`, `.changes` and `.run` only.

Key invariants:
    - None: this file holds composition only; no command logic is defined here.

See Also:
    - .claude/roadmap.md step 11.12 for this package's own deliverable.
    - docs/waggle/enrolment.md for the ceremony `invite` starts and `revoke` ends.
    - docs/runbooks/enrolling-a-device.md for the whole path an operator follows.

Public API:
    - app: the `hive swarm` Typer app (`invite`, `list`, `revoke`, `access`, `shield`, `promote`,
      `demote`, `run`).
"""

import typer

from hivemind.cli.swarm.changes import (
    access_command,
    demote_command,
    promote_command,
    revoke_command,
    shield_command,
)
from hivemind.cli.swarm.context import swarm_callback
from hivemind.cli.swarm.invite import invite_command
from hivemind.cli.swarm.listing import list_command
from hivemind.cli.swarm.run import run_command

__all__ = ["app"]

app = typer.Typer(
    name="swarm",
    help="Invite, list, change, revoke and run goals on the devices of the Swarm.",
)
app.callback()(swarm_callback)
app.command("invite")(invite_command)
app.command("list")(list_command)
app.command("revoke")(revoke_command)
app.command("access")(access_command)
app.command("shield")(shield_command)
app.command("promote")(promote_command)
app.command("demote")(demote_command)
app.command("run")(run_command)
