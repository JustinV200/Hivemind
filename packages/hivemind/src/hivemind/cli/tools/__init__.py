"""Assemble `hive tools`: list, show, request, retire, roll back and quarantine Royal Jelly tools.

Roadmap step 9.10 gives the Comb Registry (the catalogue of promoted Royal Jelly tools) and the
Quarantine Comb (the sandbox every new tool must pass) their operator commands. They would not fit
codingrules section 5.1's 300-line file limit as one module, so they are split the way
`hivemind.cli.honey` splits its own: `listing.py` (`list`, `show`), `lifecycle.py` (`retire`,
`rollback`), `request.py` (`request`, and the `RequestSubmitter` seam the Lab's promotion flow
plugs into), `quarantine.py` (`quarantine PATH`), a shared `context.py` (the group's `--manifest`,
how errors exit) and `render.py` (printing). This face only wires them together (codingrules
section 5.4). `--manifest` belongs to the group, so it comes right after `tools` and before the
subcommand (`hive tools --manifest hive.toml list`); a tool is HIVE scope unless `--cell ID` names
a Cell.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts `app`
    under the `tools` command-group name. Calls into `hivemind.cli.tools.context`, `.listing`,
    `.lifecycle`, `.request` and `.quarantine` only.

Key invariants:
    - None: this file holds composition only; no command logic is defined here.

See Also:
    - .claude/roadmap.md step 9.10 for this package's own deliverable.
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md and docs/adr/0047-quarantine-comb-gates-
      and-sandbox.md for what these commands drive.

Public API:
    - app: the `hive tools` Typer app (`list`, `show`, `request`, `retire`, `rollback`,
      `quarantine`).
"""

import typer

from hivemind.cli.tools.context import tools_callback
from hivemind.cli.tools.lifecycle import retire_command, rollback_command
from hivemind.cli.tools.listing import list_command, show_command
from hivemind.cli.tools.quarantine import quarantine_command
from hivemind.cli.tools.request import request_command

__all__ = ["app"]

app = typer.Typer(
    name="tools",
    help="List, request, retire and quarantine Royal Jelly tools in the Comb Registry.",
)
app.callback()(tools_callback)
app.command("list")(list_command)
app.command("show")(show_command)
app.command("request")(request_command)
app.command("retire")(retire_command)
app.command("rollback")(rollback_command)
app.command("quarantine")(quarantine_command)
