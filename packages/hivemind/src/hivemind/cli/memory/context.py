"""Share the small helpers every `hive memory` submodule needs: identity, MemoryContext, db path.

Split out of what would otherwise be one large `cli/memory.py` (codingrules section 5.1's 300-line
file limit) into `hivemind.cli.memory`'s own sub-package; this module is the one piece every
sibling (`show`, `pins`, `compact`, `wax`) imports, so the manifest-to-identity conversion is
written in exactly one place.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.memory.show`, `.pins`,
    `.compact` and `.wax`. Calls into `hivemind.cell`, `hivemind.brood_chamber` and
    `hivemind.memory` only.

Key invariants:
    - None: this module holds pure helpers only, no state of its own.

See Also:
    - hivemind.cli.stores for resolve_db/load_manifest_or_exit, the composition-root counterparts
      these helpers build on top of once a manifest is already loaded.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.brood_chamber import ChamberIdentity
from hivemind.cell import HoneyClearance
from hivemind.manifest import HiveManifest
from hivemind.memory import MemoryContext, MemoryIdentity, MemoryStore
from waggle.clock import SystemClock

__all__ = ["WIDEST", "chamber_identity", "memory_context", "resolved_db"]

# The widest read allowance this package's own reconstruction queries use; `hivemind.memory.
# assemble` itself re-filters every candidate down to the real principal's own `--clearance`.
WIDEST = HoneyClearance.C2


def resolved_db(loaded: HiveManifest, db: Path | None) -> Path:
    """Return `db` unchanged when given, else `loaded`'s own `[hive] db`."""
    return db if db is not None else loaded.resolve_path(loaded.hive.db)


def chamber_identity(manifest: HiveManifest, actor: str) -> ChamberIdentity:
    """Build a ChamberIdentity stamped with `actor`, for a read or a write in this package."""
    return ChamberIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=actor)


def memory_context(manifest: HiveManifest, store: MemoryStore, actor: str) -> MemoryContext:
    """Build a MemoryContext over `store`, stamped with `actor`, for a write in this package."""
    identity = MemoryIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=actor)
    return MemoryContext(store=store, identity=identity, clock=SystemClock())
