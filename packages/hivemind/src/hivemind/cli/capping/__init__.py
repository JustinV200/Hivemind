"""Assemble `hive capping`: the proposal queue, one proposal's history, and sampled audit.

Roadmap step 3.21 gave `hivemind.cli` a `capping` command group (`queue`, `show`); roadmap step
4.11 added a third piece, `audit sample|rates`, large enough on its own that combining all three
in one flat module would break codingrules section 5.1's 300-line file limit. Split here the same
way `hivemind.cli.memory` and `.readback` split theirs: `queue.py` holds `queue`/`show` (and
`track_proposals`, reused by `sample.py`), `sample.py` holds the `audit` sub-group; this face only
wires them together (codingrules section 5.4: no logic of its own).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts `app`
    under the `capping` command-group name. Calls into `hivemind.cli.capping.queue` and `.sample`
    only.

Key invariants:
    - None: this file holds composition only; no command logic is defined here.

See Also:
    - .claude/roadmap.md step 3.21 for queue/show's own deliverable; step 4.11 for audit's.
    - hivemind.cli.capping.queue, .sample for the modules under this face.

Public API:
    - app: the `hive capping` Typer app (`queue`, `show`, `audit sample|rates`).
"""

import typer

from hivemind.cli.capping.queue import queue_command, show_command
from hivemind.cli.capping.sample import app as audit_app

__all__ = ["app"]

app = typer.Typer(name="capping", help="Read the Capping proposal queue from the Pheromone Trail.")
app.command("queue")(queue_command)
app.command("show")(show_command)
app.add_typer(audit_app, name="audit")
