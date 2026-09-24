"""Hold the pending-confirmation table: requests held until a person confirms them.

A device no person types at cannot step up, so what it asks for that needs step-up waits here
until a person confirms it from an interactive device that has just stepped up (ADR-0033).
``protocol`` defines ``PendingTable`` and the two rules every implementation applies (put PENDING,
settle once along an edge from the expected status); ``sqlite`` keeps it in the Entrance tables
(``entrance_pending``, migration 0002); ``memory`` in a dict.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Used by
    ``hivemind.entrance.auth.confirm``; reached through ``EntranceStore.pending``. Calls into
    ``hivemind.common`` and ``hivemind.entrance.auth.confirm``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - hivemind.entrance.auth.confirm.state for the state machine.

Public API:
    - PendingTable, check_new_pending, check_pending_event, settle_pending: the protocol and its
      rules, every change with its trail event (protocol).
    - SqlitePendingTable: the durable table (sqlite).
    - MemoryPendingTable: the in-memory table (memory).
"""

from hivemind.entrance.store.pending.memory import MemoryPendingTable
from hivemind.entrance.store.pending.protocol import (
    PendingTable,
    check_new_pending,
    check_pending_event,
    settle_pending,
)
from hivemind.entrance.store.pending.sqlite import SqlitePendingTable

__all__ = [
    "MemoryPendingTable",
    "PendingTable",
    "SqlitePendingTable",
    "check_new_pending",
    "check_pending_event",
    "settle_pending",
]
