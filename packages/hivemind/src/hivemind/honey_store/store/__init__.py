"""Define HoneyStore, the Honey Store's persistence protocol, its SQL builder and its SQLite home.

`protocol.py` fixes the seam every implementation honours; `fts.py` is pure (no SQLite), building
a safe FTS5 MATCH string from arbitrary text; `sqlite/` is the durable implementation, split by
table responsibility. `open_honey_store`, the composition-root constructor a Hive Manifest's own
path builds, lives in `hivemind.cli.stores` (a later dispatch), not here, matching how
`hivemind.pheromone`/`hivemind.memory`'s own `open_*` constructors live in `cli/stores.py` rather
than in the store package itself.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Read by
    `hivemind.honey_store.nectar`, `.ripening`, `.honey` and `.browse` (later dispatches) and by
    `hive honey`. Calls into `hivemind.honey_store.errors`, `.models`, `.schema` and
    `hivemind.pheromone` only.

Key invariants:
    - Every mutation method on `HoneyStore` commits its row and its `HoneyEvent` in one
      transaction (codingrules section 12).

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under honey_store.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the decisions this package encodes.

Public API:
    - HoneyStore, NectarAdded, HoneyProposal, PruneResult, PruneEvents (protocol): the
      persistence protocol and its own small return shapes.
    - build_match, MAX_MATCH_TOKENS (fts): a safe FTS5 MATCH string from arbitrary text.
    - SqliteHoneyStore (sqlite): the durable implementation.
"""

from hivemind.honey_store.store.fts import MAX_MATCH_TOKENS, build_match
from hivemind.honey_store.store.protocol import (
    HoneyProposal,
    HoneyStore,
    NectarAdded,
    NectarEvents,
    PruneEvents,
    PruneResult,
)
from hivemind.honey_store.store.sqlite import SqliteHoneyStore

__all__ = [
    "MAX_MATCH_TOKENS",
    "HoneyProposal",
    "HoneyStore",
    "NectarAdded",
    "NectarEvents",
    "PruneEvents",
    "PruneResult",
    "SqliteHoneyStore",
    "build_match",
]
