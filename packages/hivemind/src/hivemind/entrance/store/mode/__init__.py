"""Hold the Entrance mode's table: one persisted row, changed only with its event.

The Entrance Reducer's mode (``OPEN`` or ``REDUCED``) is persisted so a restart comes back in the
mode it left (codingrules Appendix C, "Entrance mode"). ``protocol`` defines ``ModeTable`` and the
rule both implementations apply; ``sqlite`` keeps the row in the Entrance tables
(``entrance_mode``, migration 0002) and writes each change's event in the same transaction;
``memory`` keeps it in a field for tests and demos.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Used by
    ``hivemind.entrance.reducer``; reached through ``EntranceStore.entrance_mode``. Calls into
    ``hivemind.common``, ``hivemind.entrance.reducer`` and ``hivemind.pheromone``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - hivemind.entrance.reducer for the machine.

Public API:
    - ModeTable, check_mode_change: the protocol and its rule (protocol).
    - SqliteModeTable: the durable row (sqlite).
    - MemoryModeTable: the in-memory row (memory).
"""

from hivemind.entrance.store.mode.memory import MemoryModeTable
from hivemind.entrance.store.mode.protocol import ModeTable, check_mode_change
from hivemind.entrance.store.mode.sqlite import SqliteModeTable

__all__ = ["MemoryModeTable", "ModeTable", "SqliteModeTable", "check_mode_change"]
