"""Turn a loaded Hive Manifest into a running Hive: the compose package.

Roadmap step 3.21's second half: `hivemind.cli` is Layer 7 and the only place a `hivemind.
manifest.HiveManifest` is ever turned into deps (codingrules section 13); this package is that
composition root's own home, split from `hive.py`'s own 300-line budget into `links.py` (the one
Queen<->Warden Waggle link this phase uses) and `deps.py` (every manifest-to-deps conversion,
`WardenDeps` and `QueenDeps` included). `build_hive` is the one entry point every command that
needs a running Hive (`hive run`, roadmap step 3.21) calls; `run_hive` and `run_goal` are the two
things a caller does with what it returns.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.run` and by every test
    that drives the kernel end to end against a `hivemind.llm.FakeLLMProvider`. Calls into
    `hivemind.brood_chamber`, `hivemind.cell`, `hivemind.cli.stores`, `hivemind.llm`,
    `hivemind.manifest`, `hivemind.pheromone`, `hivemind.queen`, `hivemind.wardens` and waggle.

Key invariants:
    - `build_hive` is the only function in this package (or below `cli`) that reads a
      `HiveManifest` directly; every other collaborator it builds takes a manifest slice.
    - Nothing in this package branches on `cell.kind` or a provider's own `name`
      (`scripts/check_no_kind_branches.py`); see `hivemind.cli.compose.deps`'s own docstring for
      the one place a `kind` string (never a `CellKind`) does select a factory.

See Also:
    - .claude/codingrules.md section 13 for the composition-root rule this package exists to keep.
    - .claude/roadmap.md step 3.21 for this package's own roadmap bullet.
    - hivemind.cli.compose.hive for Hive, HiveStores, GoalReport, build_hive, run_hive and
      run_goal, the module every name below is re-exported from (bar HiveStores, from .deps).
    - hivemind.cli.compose.deps for the manifest-to-deps conversions build_hive composes.
    - hivemind.cli.compose.links for the Queen<->Warden Waggle link build_hive composes.

Public API:
    - Hive: everything `hive run` needs a handle on (manifest, stores, registry, fanner, source,
      warden, queen, warden_link, clock).
    - HiveStores: the trail, chamber and memory store one Hive shares a SQLite file for.
    - GoalReport: what `run_goal` returns.
    - build_hive: the one HiveManifest -> Hive composition function.
    - run_hive: an asynccontextmanager that leases the Cell, runs the Queen and Warden, and tears
      both down on exit.
    - run_goal: submit a goal and poll until its tasks are terminal or a timeout elapses.
"""

from hivemind.cli.compose.deps import HiveStores
from hivemind.cli.compose.hive import GoalReport, Hive, build_hive, run_goal, run_hive

__all__ = ["GoalReport", "Hive", "HiveStores", "build_hive", "run_goal", "run_hive"]
