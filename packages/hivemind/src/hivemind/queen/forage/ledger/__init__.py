"""The Forage ledger: the Queen's live book of capacity, grants and headroom, backed by SQLite.

Roadmap step 4.7. `ForageLedger` (`book.py`) is the live table every other module in this package
reads and writes: every Cell's latest `ForageCapacity`, every Warden's own
`LocalPoolReport` (reported, never granted -- codingrules section 8.10), every live
`ForageGrant`, the `RoyalReserve`, and the `Headroom` it computes from them. `LedgerStore`
(`store_protocol.py`) is the persistence seam Appendix C's "Forage ledger (SQLite) | Yes" row
asks for, with `InMemoryLedgerStore` (`store_memory.py`) for tests and a Queen with no database
file yet, and `SqliteLedgerStore` (`store_sqlite.py`) as the durable implementation.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.forage`.
    Built once per Queen; read and written by `hivemind.queen.forage.requests` and `.grants`, and
    by `hivemind.queen.ticks.liveness` for heartbeat renewal and expiry. Calls into
    `hivemind.forage`, `hivemind.common` and waggle only.

Key invariants:
    - `ForageLedger._grants` never holds a terminal-state (REVOKED) grant; see `book.py`.
    - Every `LedgerStore` implementation honours the same upsert/idempotent-delete contract; see
      `store_protocol.py`.

See Also:
    - .claude/roadmap.md step 4.7 for the ledger's own field-by-field description.
    - .claude/codingrules.md section 8.10 for the ledger's role dividing dividing Forage.
    - .claude/codingrules.md Appendix C for the durability contract this package implements.

Public API:
    - ForageLedger: the live book (book).
    - Headroom, LocalPoolReport: the ledger's own small value types (model).
    - LedgerStore: the persistence protocol (store_protocol).
    - InMemoryLedgerStore: the non-durable implementation (store_memory).
    - SqliteLedgerStore, apply_ledger_migrations: the durable implementation (store_sqlite).
"""

from hivemind.queen.forage.ledger.book import ForageLedger
from hivemind.queen.forage.ledger.model import Headroom, LocalPoolReport
from hivemind.queen.forage.ledger.store_memory import InMemoryLedgerStore
from hivemind.queen.forage.ledger.store_protocol import LedgerStore
from hivemind.queen.forage.ledger.store_sqlite import SqliteLedgerStore, apply_ledger_migrations

__all__ = [
    "ForageLedger",
    "Headroom",
    "InMemoryLedgerStore",
    "LedgerStore",
    "LocalPoolReport",
    "SqliteLedgerStore",
    "apply_ledger_migrations",
]
