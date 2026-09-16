"""Assemble `hive memory`: hot state, pins, compaction and Cell Wax, from this package's modules.

Roadmap step 4.11 gave `hivemind.cli` a fourth command group whose own deliverable (`show`, `pins
add|list|remove`, `compact`, `wax list|propose|clear`) would not fit codingrules section 5.1's
300-line file limit as one flat module, so it is split here the same way `hivemind.cli.compose`
and `hivemind.cli.readback` already split theirs: one sub-module per responsibility (`show.py`,
`pins.py`, `compact.py`, `wax.py`), a shared `context.py` for the identity/db-path helpers every
one of them needs, and this face, which only wires the four together (codingrules section 5.4: no
logic of its own).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts `app`
    under the `memory` command-group name. Calls into `hivemind.cli.memory.show`, `.pins`,
    `.compact` and `.wax` only.

Key invariants:
    - None: this file holds composition only; no command logic is defined here.

See Also:
    - .claude/roadmap.md step 4.11 for this package's own deliverable, verbatim.
    - hivemind.cli.memory.show, .pins, .compact, .wax for the modules under this face.

Public API:
    - app: the `hive memory` Typer app (`show`, `pins`, `compact`, `wax`).
"""

import typer

from hivemind.cli.memory.compact import compact_command
from hivemind.cli.memory.pins import app as pins_app
from hivemind.cli.memory.show import show_command
from hivemind.cli.memory.wax import app as wax_app

__all__ = ["app"]

app = typer.Typer(name="memory", help="Inspect a bee's hot state; manage pins, wax and compaction.")
app.command("show")(show_command)
app.command("compact")(compact_command)
app.add_typer(pins_app, name="pins")
app.add_typer(wax_app, name="wax")
