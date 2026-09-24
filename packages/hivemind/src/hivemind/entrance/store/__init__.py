"""Hold the Entrance tables: the EntranceStore protocol, its SQLite and in-memory implementations.

The Entrance tables, the Hive Entrance's (the Hive's one HTTP door) own SQLite tables, keep the
operator's password hash, the enrolled devices and their invites
(codingrules Appendix C: password hash and public keys only), in the Hive's own ``[hive] db``
file under their own migration series. ``protocol`` defines ``EntranceStore`` and the three rules
both implementations apply (a device enters only at the state machine's entry, a status changes
only along an edge from the status the caller expected, an invite admits once before it expires);
``sqlite`` is the durable store and ``memory`` the fake for tests and demos. Sessions (0002), push
subscriptions (0003) and goals by device (0004) join in later steps.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Constructed by the
    Entrance's composition root (``entrance/app.py``, a later step), a ``hive entrance`` command
    or a test; used by ``hivemind.entrance.enrol`` and the Entrance's routes. Calls into
    ``hivemind.common`` (SQLite, migrations) and ``hivemind.entrance.enrol``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No status change bypasses ``hivemind.entrance.enrol.state``: ``update_device_status`` is the
      only way a status moves, and it names the status it expects to move from.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the one-file decision.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.

Public API:
    - EntranceStore, DeviceChanges: the protocol and a status change's field updates.
    - check_new_device, transition_device, use_invite: the rules every implementation applies.
    - SqliteEntranceStore, apply_entrance_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      store and its migration series.
    - MemoryEntranceStore: the in-process store for tests and demos.
"""

from hivemind.entrance.store.memory import MemoryEntranceStore
from hivemind.entrance.store.protocol import (
    DeviceChanges,
    EntranceStore,
    check_new_device,
    transition_device,
    use_invite,
)
from hivemind.entrance.store.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteEntranceStore,
    apply_entrance_migrations,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "DeviceChanges",
    "EntranceStore",
    "MemoryEntranceStore",
    "SqliteEntranceStore",
    "apply_entrance_migrations",
    "check_new_device",
    "transition_device",
    "use_invite",
]
