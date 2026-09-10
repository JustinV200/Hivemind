"""Group the CLI's own read-only state-reconstruction commands: cells, inbox and wardens.

Roadmap step 3.21 (second half) added three commands that all share one shape: each reconstructs
what it shows from a stored artifact this Hive already writes for another reason (the Hive Stand's
own config, the Brood Chamber, the Pheromone Trail) rather than a live link into a running `hive
run`'s Queen process, which v0 has none of (the Landing Board, roadmap phase 10, is where one
arrives). Grouped into this package, alongside `hivemind.cli.compose`, so `hivemind.cli` itself
stays within codingrules section 5.6's ten-module-per-directory limit; each module keeps its own
`Typer` app under its own name (`cells.app`, `inbox.app`, `wardens.app`), plain values re-exported
here under distinct names since a caller (`hivemind.cli.app`) needs all three at once.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts each
    re-exported Typer app under its own command-group name. Calls into `hivemind.cli.readback.
    cells`, `.inbox` and `.wardens` only.

Key invariants:
    - None: this file holds re-exports and __all__ only; no command logic is defined here
      (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 5.6 for the module-count limit this package exists to keep
      `hivemind.cli` under.
    - .claude/roadmap.md step 3.21 for these three commands' own roadmap bullet.
    - hivemind.cli.readback.cells, .inbox, .wardens for the modules under this face.

Public API:
    - cells_app: `hive cells list`'s own Typer app.
    - inbox_app: `hive inbox` / `hive inbox answer`'s own Typer app.
    - wardens_app: `hive wardens list`'s own Typer app.
"""

from hivemind.cli.readback.cells import app as cells_app
from hivemind.cli.readback.inbox import app as inbox_app
from hivemind.cli.readback.wardens import app as wardens_app

__all__ = ["cells_app", "inbox_app", "wardens_app"]
