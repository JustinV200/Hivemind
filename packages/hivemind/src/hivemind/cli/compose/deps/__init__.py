"""Convert a loaded Hive Manifest into HiveStores, a Fanner, and both deps bags.

Codingrules section 13: the composition root is the only place a `HiveManifest` is turned into the
deps every subsystem actually takes; this package is that conversion for roadmap step 3.21's second
half, one function per collaborator so `hivemind.cli.compose.hive.build_hive` stays a short list of
calls. It is a package, not a module, because the two bags and the parts they share outgrew one
file once phases 8 and 9 both added their wiring (codingrules 5.2), and `hivemind.cli.compose`
already holds ten modules (codingrules 5.6): `parts` holds what every builder shares (`HiveStores`,
`HiveParts`, the stores, the Hive Stand's source, the Fanner and the ledger), `warden` builds the
Hive Stand's own `WardenDeps` and `queen` the `QueenDeps`. Roadmap step 10.3: `build_enforcer`
builds the one Guard `Enforcer` (over `[guard]`'s policy) that the Queen and the Hive Stand's
Warden share, carried on `HiveParts.enforcer` (it lives in `hivemind.cli.compose.guard` since
roadmap step 10.3a, beside the policy that names the Hive's own state, and is re-exported here).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive` and the readback commands. Calls into its own three
    modules and `hivemind.cli.compose.guard` only; see each module for what it reaches.

Key invariants:
    - Every identity this package builds (`MemoryIdentity`, `ChamberIdentity`, `CellIdentity`)
      stamps `actor="system"`: the composition root itself is not a bee, and every write it makes
      on a bee's behalf is attributed the same way `tests.builders.queen`/`.wardens` already do.
    - Every name a caller used to import from the one module is still importable from here.

See Also:
    - .claude/codingrules.md section 13 for "the composition root is the only place a HiveManifest
      is converted".
    - hivemind.cli.compose.hive for build_hive, this package's main caller.

Public API:
    - HiveStores, HiveParts, open_default_stores, build_hive_stand_source, build_fanner,
      build_ledger (parts): the shared stores and collaborators.
    - HIVE_STAND_LEASE, build_warden_deps, build_cell_warden_deps (warden): the Hive Stand's
      Warden's bag, and the part of it any Real Cell's Warden shares (a Swarm device's).
    - build_queen_deps (queen): the Queen's bag.
    - build_enforcer (hivemind.cli.compose.guard, re-exported): the one Guard Enforcer.
"""

# Roadmap step 10.3's one Enforcer; built beside the policy it decides against, re-exported here.
from hivemind.cli.compose.deps.parts import (
    HiveParts,
    HiveStores,
    build_fanner,
    build_hive_stand_source,
    build_ledger,
    open_default_stores,
)
from hivemind.cli.compose.deps.queen import build_queen_deps
from hivemind.cli.compose.deps.warden import (
    HIVE_STAND_LEASE,
    build_cell_warden_deps,
    build_warden_deps,
)
from hivemind.cli.compose.guard import build_enforcer

__all__ = [
    "HIVE_STAND_LEASE",
    "HiveParts",
    "HiveStores",
    "build_cell_warden_deps",
    "build_enforcer",
    "build_fanner",
    "build_hive_stand_source",
    "build_ledger",
    "build_queen_deps",
    "build_warden_deps",
    "open_default_stores",
]
