"""The in-Cell Warden entry point: what `images/base-ubuntu`'s ENTRYPOINT runs (roadmap step 5.5).

A Virtual Cell exposes no inbound port (codingrules section 15), so the Warden that will
eventually own it cannot be reached -- it has to reach out. This package is the composition root
that makes that first connection real: `config` turns the container's `HIVEMIND_*` environment
(read once, in `hivemind.manifest.env`, never here) into typed ids, Ed25519 keys and a probed
Cell description; `link` is `CellLink`, the small `waggle.loop.TickLoop` that dials the Queen's
Waggle URL, sends this Cell's one signed `CellReady`, heartbeats on an interval, and stops on a
`Shutdown` or `CellTeardownRequest`; `main` wires both together as `main()`, the console-script
target `packages/hivemind/pyproject.toml` registers and the Dockerfile's ENTRYPOINT invokes.
Building a full task-executing `hivemind.wardens.warden.Warden` on top of this same link is the
next step, once the Queen side can accept one (this dispatch's own report names exactly what is
missing there); `hivemind.wardens.spawn.in_cell.InCellSpawnSource` -- already used here to probe
this Cell -- is what that Warden's own `WardenDeps.source` will be.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). The one composition root for a Virtual Cell's own
    process; nothing above it. Calls into `hivemind.cell`, `hivemind.common`, `hivemind.manifest`,
    `hivemind.pheromone`, `hivemind.wardens.spawn` and waggle only.

Key invariants:
    - `os.environ` is read exactly once per process start (`hivemind.cli.in_cell.main.main`),
      always through `hivemind.manifest.env.read_in_cell_env` (codingrules section 13).
    - Signing is mandatory: `hivemind.cli.in_cell.config.build_runtime_config` refuses to start
      without both this Cell's own signing key and the Queen's verify key (roadmap step 1.7).

See Also:
    - .claude/roadmap.md step 5.5 for this package's own roadmap bullet.
    - images/base-ubuntu/README.md for the image layer that runs `main()`.
    - hivemind.wardens.spawn.in_cell for InCellSpawnSource, the `in_cell` Warden spawn strategy
      this package's own composition root builds and probes.

Public API:
    - InCellRuntimeConfig, build_runtime_config: turn InCellEnv into typed config (config).
    - CellLinkDeps, CellLink: connect, announce, heartbeat, stop (link).
    - main, run_in_cell_warden: the console-script entry point and its async body (main).
"""

from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.cli.in_cell.link import CellLink, CellLinkDeps
from hivemind.cli.in_cell.main import main, run_in_cell_warden

__all__ = [
    "CellLink",
    "CellLinkDeps",
    "InCellRuntimeConfig",
    "build_runtime_config",
    "main",
    "run_in_cell_warden",
]
