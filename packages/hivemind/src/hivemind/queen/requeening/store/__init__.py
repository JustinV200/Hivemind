"""Keep what a restarted Queen needs and no other store holds: her snapshot, what she handled.

Requeening (roadmap 13.1, ADR-0062) reads most of what it restores from stores that were durable
all along. This package is the rest: the Queen's own newest snapshot (roadmap 13.2), the Alarms and
questions she handled from Wardens (so a resend after her restart is dropped, and a question's
answer can still be forwarded), and the port her Virtual Cell listener bound last. One Protocol,
an in-process implementation for tests and a Queen with no database, and a SQLite one with its own
numbered migration beside it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), a sub-package of
    `hivemind.queen.requeening`. Held by `RequeeningBook`; opened in production by
    `hivemind.cli.stores.open_requeening_store`. Calls into `hivemind.common` (migrations,
    sqlite), `hivemind.queen.requeening.models` and waggle only.

Key invariants:
    - Only the newest snapshot is kept.
    - A question's wiring, once noted, is never lost to a later plain note for the same envelope.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md.

Public API:
    - RequeeningStore: the Protocol (protocol).
    - MemoryRequeeningStore: in this process only (memory).
    - SqliteRequeeningStore, SUBSYSTEM, MIGRATIONS_PACKAGE: over `[hive] db` (sqlite).
"""

from hivemind.queen.requeening.store.memory import MemoryRequeeningStore
from hivemind.queen.requeening.store.protocol import RequeeningStore
from hivemind.queen.requeening.store.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteRequeeningStore,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "MemoryRequeeningStore",
    "RequeeningStore",
    "SqliteRequeeningStore",
]
