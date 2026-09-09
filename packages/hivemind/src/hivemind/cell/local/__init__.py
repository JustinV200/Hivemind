"""Provide the local Cell backend for the Hive Stand, the Queen's own machine and first Real Cell.

It doubles as the default home of every Warden (the per-Cell supervisor) and needs nothing beyond
the standard library (codingrules section 4: "The Hive Stand Real Cell lives in `cell/local/`
because it needs nothing beyond the standard library"). `HiveStandConfig` is the package's own,
already-resolved view of the manifest's `[hive_stand]` section; `probe_host`/`refresh_live` read
this machine's platform and capacity, POSIX and Windows side by side; `HiveStandSource` is the
`RealCellSource` that hands out the Hive Stand's one Cell, leased and released, never provisioned;
`LocalProcessSession` is the `CellSession` a lease opens, running commands as real OS processes
under a scratch-quota watchdog; `HiveStandLeaseReleaser` is what a lease's `release()` delegates
to, killing survivors, restoring touched paths and removing scratch.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the `cell` package. Constructed
    once by the composition root (`cli/stores.py`, a later step) from a loaded `HiveManifest`;
    everything above `cell` reaches this package only through the `RealCellSource` and
    `CellSession` Protocols it implements, never by name. Calls into `hivemind.cell` (its own
    parent package), `hivemind.forage`, `hivemind.manifest.schema.core`, `hivemind.pheromone` and
    the standard library only.

Key invariants:
    - Every name in `__all__` here is re-exported from exactly one sibling module; this file
      holds no logic of its own (codingrules 5.4).
    - This is the one sub-package under `hivemind.cell` allowed to import `subprocess` or
      `asyncio.subprocess` (codingrules section 4; the root `pyproject.toml` import-linter
      contract "subprocess only from Cell sessions and hive backends" enforces it).

See Also:
    - .claude/roadmap.md step 3.11 for this package's own step.
    - docs/adr/0010-cells-are-real-or-virtual-terminal-first.md for why the Hive Stand is the
      first Real Cell source and needs only the standard library.
    - hivemind.cell for the Cell abstraction (Cell, CellSession, RealCellSource, RealCellLease)
      this package's classes implement or build on.

Public API:
    - HiveStandConfig: the Hive Stand's own settings, resolved once from the manifest
      (hivemind.cell.local.config).
    - ProbeResult, probe_host, refresh_live, DEFAULT_MAX_SUB_BEES: this host's platform,
      capabilities and capacity (hivemind.cell.local.probe).
    - ScratchQuota, DirectorySizer, QUOTA_SAMPLE_INTERVAL_S, directory_size_bytes: how a lease's
      scratch usage is measured and capped (hivemind.cell.local.quota).
    - LocalProcessSession: a CellSession over a real OS process (hivemind.cell.local.session).
    - HiveStandLeaseReleaser, kill_process_tree, KILL_GRACE_S: leaving the Hive Stand as found on
      release (hivemind.cell.local.releaser).
    - HiveStandSource: the RealCellSource for the Hive Stand's one Cell
      (hivemind.cell.local.source).
"""

from hivemind.cell.local.config import HiveStandConfig
from hivemind.cell.local.probe import DEFAULT_MAX_SUB_BEES, ProbeResult, probe_host, refresh_live
from hivemind.cell.local.quota import (
    QUOTA_SAMPLE_INTERVAL_S,
    DirectorySizer,
    ScratchQuota,
    directory_size_bytes,
)
from hivemind.cell.local.releaser import KILL_GRACE_S, HiveStandLeaseReleaser, kill_process_tree
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cell.local.source import HiveStandSource

__all__ = [
    "DEFAULT_MAX_SUB_BEES",
    "KILL_GRACE_S",
    "QUOTA_SAMPLE_INTERVAL_S",
    "DirectorySizer",
    "HiveStandConfig",
    "HiveStandLeaseReleaser",
    "HiveStandSource",
    "LocalProcessSession",
    "ProbeResult",
    "ScratchQuota",
    "directory_size_bytes",
    "kill_process_tree",
    "probe_host",
    "refresh_live",
]
