"""Re-export Leaving, ApprovedBy, LeavingsStore and its two implementations: the leavings package.

The Leavings ledger (roadmap steps 5.0a-5.0e) is what a task may leave behind on a Cell after its
lease is released: scratch is still removed wholesale, and a Cell is still left as found, *plus
exactly the paths this ledger lists* (roadmap phase 5 preamble). `hivemind.cell.leavings.model`
defines `Leaving` (one ledger row) and `ApprovedBy` (who allowed it to stay);
`hivemind.cell.leavings.store_protocol` defines `LeavingsStore` (codingrules 8.1: a Protocol at
every seam) and `check_leaving_event`; `.store_memory.InMemoryLeavingsStore` and
`.store_sqlite.SqliteLeavingsStore` are its two implementations, and `.migrations` is the SQL
series the SQLite implementation applies. This file is the package's face: a caller writes `from
hivemind.cell.leavings import LeavingsStore` without knowing the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell`. Used by
    `hivemind.cell.local.releaser` (`record_leaving`, roadmap step 5.0a), `hivemind.cli.stores`
    (`open_leavings`) and `hive cells leavings list|remove` (`hivemind.cli.readback.leavings`).
    Not re-exported from `hivemind.cell`'s own top-level face, the same way `hivemind.cell.local`
    is not: a caller that needs it imports this sub-package directly.

Key invariants:
    - This file holds re-exports and __all__ only; the four modules behind it are where every
      name is actually defined.
    - Sibling modules inside `hivemind.cell` import `model`, `store_protocol`, `store_memory` and
      `store_sqlite` by their full module path, never through this face (mirrors
      `hivemind.brood_chamber.store`'s own rule): `hivemind.cell.lease` imports `ApprovedBy` from
      `hivemind.cell.leavings.model` directly, for instance.

See Also:
    - .claude/roadmap.md step 5.0a for this package's own roadmap bullet.
    - hivemind.brood_chamber.store for the package-face pattern this package follows.
    - hivemind.cell.lease for RestoreRecord.persist/approved_by/reason, the per-lease bookkeeping
      a Leaving is built from at release.
    - hivemind.cell.local.releaser for HiveStandLeaseReleaser, this package's one production writer.

Public API:
    - ApprovedBy, Leaving, SHA256_HEX_CHARS: the ledger's own value types (model).
    - LeavingsStore, check_leaving_event: the store protocol and its event guard (store_protocol).
    - InMemoryLeavingsStore: an in-process LeavingsStore for tests and demos (store_memory).
    - SqliteLeavingsStore, apply_leavings_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      LeavingsStore (store_sqlite).
"""

from hivemind.cell.leavings.model import SHA256_HEX_CHARS, ApprovedBy, Leaving
from hivemind.cell.leavings.store_memory import InMemoryLeavingsStore
from hivemind.cell.leavings.store_protocol import LeavingsStore, check_leaving_event
from hivemind.cell.leavings.store_sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteLeavingsStore,
    apply_leavings_migrations,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SHA256_HEX_CHARS",
    "SUBSYSTEM",
    "ApprovedBy",
    "InMemoryLeavingsStore",
    "Leaving",
    "LeavingsStore",
    "SqliteLeavingsStore",
    "apply_leavings_migrations",
    "check_leaving_event",
]
