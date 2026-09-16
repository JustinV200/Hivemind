"""The Forage ledger: the Queen's live book of capacity, grants and headroom, backed by SQLite.

Roadmap step 4.7. `ForageLedger` (`book.py`) is the live table every other module in this package
reads and writes: every Cell's latest `ForageCapacity`, every Warden's own
`LocalPoolReport` (reported, never granted -- codingrules section 8.10), every live
`ForageGrant`, the `RoyalReserve`, and the `Headroom` it computes from them. Roadmap step 4.8 adds
three sub-books `ForageLedger` composes rather than reimplements: `SeatBook` (`seats.py`, a shared
source's declared seat capacity and live in-flight usage), `SpendBook` (`spend.py`, spend recorded
per goal and its headroom against a cap) and `DecisionBook` (`decisions.py`, each Cell's written
`HostingPlan` and each Warden's set `Ceilings`); `LedgerRecorder` (`recorder.py`) is the Fanner
(the seat meter every model call passes through)-facing feed for `SeatBook` and per-grant/per-goal
spend, implementing `hivemind.llm.fanner.LlmEventRecorder` without this package ever importing
`hivemind.llm` (queen may import llm; the reverse never happens -- see that module's own docstring
for the layering this keeps clean). `LedgerStore` (`store_protocol.py`) is the persistence seam
Appendix C's "Forage ledger (SQLite) | Yes" row asks for, with `InMemoryLedgerStore`
(`store_memory.py`) for tests and a Queen with no database file yet, and `SqliteLedgerStore`
(`store_sqlite.py`) as the durable implementation.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.forage`.
    Built once per Queen; read and written by `hivemind.queen.forage.requests`, `.grants`,
    `.hosting` and `.ceilings`, and by `hivemind.queen.ticks.liveness` for heartbeat renewal and
    expiry. Calls into `hivemind.forage`, `hivemind.llm.fanner` (for the `LlmEventRecorder`
    Protocol `LedgerRecorder` implements), `hivemind.common` and waggle only.

Key invariants:
    - `ForageLedger._grants` never holds a terminal-state (REVOKED) grant; see `book.py`.
    - Every `LedgerStore` implementation honours the same upsert/idempotent-delete contract; see
      `store_protocol.py`.

See Also:
    - .claude/roadmap.md step 4.7 for the ledger's own field-by-field description.
    - .claude/roadmap.md step 4.8 for the shared-seat, spend, hosting-plan and ceilings additions.
    - .claude/codingrules.md section 8.10 for the ledger's role dividing dividing Forage.
    - .claude/codingrules.md Appendix C for the durability contract this package implements.

Public API:
    - ForageLedger: the live book (book).
    - Headroom, LocalPoolReport: the ledger's own small value types (model).
    - SeatBook, SpendBook, DecisionBook: roadmap step 4.8's three sub-books (seats, spend,
      decisions).
    - LedgerRecorder: the Fanner-facing LlmEventRecorder implementation (recorder).
    - LedgerStore: the persistence protocol (store_protocol).
    - InMemoryLedgerStore: the non-durable implementation (store_memory).
    - SqliteLedgerStore, apply_ledger_migrations: the durable implementation (store_sqlite).
"""

from hivemind.queen.forage.ledger.book import ForageLedger
from hivemind.queen.forage.ledger.decisions import DecisionBook
from hivemind.queen.forage.ledger.model import Headroom, LocalPoolReport
from hivemind.queen.forage.ledger.recorder import LedgerRecorder
from hivemind.queen.forage.ledger.seats import SeatBook
from hivemind.queen.forage.ledger.spend import SpendBook
from hivemind.queen.forage.ledger.store_memory import InMemoryLedgerStore
from hivemind.queen.forage.ledger.store_protocol import LedgerStore
from hivemind.queen.forage.ledger.store_sqlite import SqliteLedgerStore, apply_ledger_migrations

__all__ = [
    "DecisionBook",
    "ForageLedger",
    "Headroom",
    "InMemoryLedgerStore",
    "LedgerRecorder",
    "LedgerStore",
    "LocalPoolReport",
    "SeatBook",
    "SpendBook",
    "SqliteLedgerStore",
    "apply_ledger_migrations",
]
