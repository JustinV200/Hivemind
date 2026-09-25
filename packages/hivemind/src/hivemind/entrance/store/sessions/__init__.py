"""Hold the session tables: SessionTable, its SQLite and in-memory forms, and the console's split.

A login at the Hive Entrance (the Hive's one HTTP door) opens a session kept by its token's SHA-256,
and every signed request spends a nonce that is refused again for twice the skew window (ADR-0041).
``protocol`` defines ``SessionTable``; ``sqlite`` keeps both in the Entrance
tables (``entrance_sessions``, ``entrance_nonces``, migration 0002); ``memory`` keeps them in
dicts, for tests and for the Hive Stand console, whose sessions are volatile by design; ``split``
routes the console's sessions to memory and every other session to the tables.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Used by
    ``hivemind.entrance.auth.session``; reached through ``EntranceStore.sessions``. Calls into
    ``hivemind.common`` and ``hivemind.entrance.auth.session.models``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A console session is never written to the Entrance tables.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for sessions.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.

Public API:
    - SessionTable, check_new_session: the protocol and its rule (protocol).
    - SqliteSessionTable: the durable table (sqlite).
    - MemorySessionTable: the in-memory table (memory).
    - SplitSessionTable: the console's sessions in memory, every other in the tables (split).
"""

from hivemind.entrance.store.sessions.memory import MemorySessionTable
from hivemind.entrance.store.sessions.protocol import SessionTable, check_new_session
from hivemind.entrance.store.sessions.split import SplitSessionTable
from hivemind.entrance.store.sessions.sqlite import SqliteSessionTable

__all__ = [
    "MemorySessionTable",
    "SessionTable",
    "SplitSessionTable",
    "SqliteSessionTable",
    "check_new_session",
]
