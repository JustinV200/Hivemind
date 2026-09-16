"""Group the CLI's own read-mostly state-reconstruction commands: cells, inbox, wardens, cluster.

Roadmap step 3.21 (second half) added three commands that all share one shape: each reconstructs
what it shows from a stored artifact this Hive already writes for another reason (the Hive Stand's
own config, the Brood Chamber, the Pheromone Trail) rather than a live link into a running `hive
run`'s Queen process, which v0 has none of (the Landing Board, roadmap phase 10, is where one
arrives). Roadmap step 4.11 adds a fourth, `cluster` (`hive cluster [provider]`/`hive cluster
status`/`hive wake`): mostly a read the same way (`status` reconstructs handled orders from the
trail), with one small write (`cluster`/`wake` append a durable `ClusterOrder` row) -- the same
shape `inbox.py`'s own `answer` command already has, so it joins this package rather than the flat
`hivemind.cli` package, which is already at codingrules section 5.6's ten-module limit without it.
Each module keeps its own `Typer` app under its own name (`cells.app`, `inbox.app`, `wardens.app`,
`cluster.app`), plain values re-exported here under distinct names since a caller (`hivemind.cli.
app`) needs all four at once; `cluster.wake_command` is re-exported separately because `hive wake`
is registered on the root app as a bare command (`hive run`'s own shape), not nested under
`cluster`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts each
    re-exported Typer app under its own command-group name, and registers `wake_command` as a bare
    `hive wake` command. Calls into `hivemind.cli.readback.cells`, `.inbox`, `.wardens` and
    `.cluster` only.

Key invariants:
    - None: this file holds re-exports and __all__ only; no command logic is defined here
      (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 5.6 for the module-count limit this package exists to keep
      `hivemind.cli` under.
    - .claude/roadmap.md step 3.21 for cells/inbox/wardens' own roadmap bullet; step 4.11 for
      cluster's.
    - hivemind.cli.readback.cells, .inbox, .wardens, .cluster for the modules under this face.

Public API:
    - cells_app: `hive cells list`'s own Typer app.
    - inbox_app: `hive inbox` / `hive inbox answer`'s own Typer app.
    - wardens_app: `hive wardens list`'s own Typer app.
    - cluster_app: `hive cluster [provider]` / `hive cluster status`'s own Typer app.
    - wake_command: `hive wake [provider]`, registered as a bare root command.
"""

from hivemind.cli.readback.cells import app as cells_app
from hivemind.cli.readback.cluster import app as cluster_app
from hivemind.cli.readback.cluster import wake_command
from hivemind.cli.readback.inbox import app as inbox_app
from hivemind.cli.readback.wardens import app as wardens_app

__all__ = ["cells_app", "cluster_app", "inbox_app", "wake_command", "wardens_app"]
