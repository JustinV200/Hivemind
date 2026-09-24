"""Hold the Entrance tables: the EntranceStore protocol, its SQLite and in-memory implementations.

The Entrance tables, the Hive Entrance's (the Hive's one HTTP door) own SQLite tables, keep the
operator's password hash, the enrolled devices and their invites
(codingrules Appendix C: password hash and public keys only), in the Hive's own ``[hive] db``
file under their own migration series. ``protocol`` defines ``EntranceStore`` and the rules both
implementations apply (a device enters only at the state machine's entry, a status changes only
along an edge from the status the caller expected, an invite admits once before it expires, a
login is recorded only on an APPROVED device and never moves a passkey's counter back, and every
entry and change carries its own ``guard.entrance_*`` Pheromone Trail event, written in the same
atomic step); ``sqlite`` is the durable store and ``memory`` the fake for tests and demos.
Roadmap step 10.5e adds four tables (migration 0002), each its own sub-package with its protocol
and both implementations, gathered by ``tables.AuthTables``, which ``EntranceStore`` extends:
``sessions`` (sessions and spent nonces, and ``SplitSessionTable``, which keeps the console's
sessions in memory), ``logins`` (failures and known networks), ``pending`` (held requests) and
``mode`` (the Entrance mode). ``link`` is the connection, thread and lock the SQLite tables share.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Constructed by the
    Entrance's composition root (``entrance/app.py``, a later step), a ``hive entrance`` command
    or a test; used by ``hivemind.entrance.enrol`` and the Entrance's routes. Calls into
    ``hivemind.common`` (SQLite, migrations), ``hivemind.entrance.enrol`` and
    ``hivemind.pheromone`` (the events it writes).

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No status change bypasses ``hivemind.entrance.enrol.state``: ``update_device_status`` (and
      ``redeem_invite`` for INVITED to PENDING) is the only way a status moves, and it names the
      status it expects to move from; ``record_login`` never moves one.
    - A state change and its trail event commit together or not at all (codingrules Appendix C).
    - A session's token, a password and a private key are never stored: hashes and public keys.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the one-file decision.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.

Public API:
    - EntranceStore, DeviceChanges, GrantChanges: the protocol, a status change's field updates
      and a re-grant's.
    - check_new_device, check_status_change, check_device_event, check_grant_change,
      transition_device, regrant_device, use_invite, apply_login: the rules every implementation
      applies.
    - SqliteEntranceStore, apply_entrance_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      store and its migration series.
    - MemoryEntranceStore: the in-process store for tests and demos.
    - AuthTables: the four tables EntranceStore extends with (tables).
    - SessionTable, SqliteSessionTable, MemorySessionTable, SplitSessionTable: sessions and
      spent nonces (sessions).
    - LoginTable, SqliteLoginTable, MemoryLoginTable: login failures and known networks (logins).
    - PendingTable, SqlitePendingTable, MemoryPendingTable: held requests (pending).
    - ModeTable, SqliteModeTable, MemoryModeTable: the Entrance mode (mode).
    - SqliteLink: the connection, thread and lock the SQLite tables share (link).
"""

from hivemind.entrance.store.link import SqliteLink
from hivemind.entrance.store.logins import LoginTable, MemoryLoginTable, SqliteLoginTable
from hivemind.entrance.store.memory import MemoryEntranceStore
from hivemind.entrance.store.mode import MemoryModeTable, ModeTable, SqliteModeTable
from hivemind.entrance.store.pending import MemoryPendingTable, PendingTable, SqlitePendingTable
from hivemind.entrance.store.protocol import (
    DeviceChanges,
    EntranceStore,
    GrantChanges,
    apply_login,
    check_device_event,
    check_grant_change,
    check_new_device,
    check_status_change,
    regrant_device,
    transition_device,
    use_invite,
)
from hivemind.entrance.store.sessions import (
    MemorySessionTable,
    SessionTable,
    SplitSessionTable,
    SqliteSessionTable,
)
from hivemind.entrance.store.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteEntranceStore,
    apply_entrance_migrations,
)
from hivemind.entrance.store.tables import AuthTables

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "AuthTables",
    "DeviceChanges",
    "EntranceStore",
    "GrantChanges",
    "LoginTable",
    "MemoryEntranceStore",
    "MemoryLoginTable",
    "MemoryModeTable",
    "MemoryPendingTable",
    "MemorySessionTable",
    "ModeTable",
    "PendingTable",
    "SessionTable",
    "SplitSessionTable",
    "SqliteEntranceStore",
    "SqliteLink",
    "SqliteLoginTable",
    "SqliteModeTable",
    "SqlitePendingTable",
    "SqliteSessionTable",
    "apply_entrance_migrations",
    "apply_login",
    "check_device_event",
    "check_grant_change",
    "check_new_device",
    "check_status_change",
    "regrant_device",
    "transition_device",
    "use_invite",
]
