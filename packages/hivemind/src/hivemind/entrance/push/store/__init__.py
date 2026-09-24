"""Hold the push tables: the SubscriptionStore protocol, its SQLite and in-memory implementations.

Push subscriptions are per device and persisted (codingrules Appendix C, ADR-0034), and each
notice's recipients are remembered so a withdrawal reaches exactly them. ``protocol`` defines
``SubscriptionStore``; ``sqlite`` is the durable store in the Hive's own database file under its
own migration series (``entrance_push``); ``memory`` is the fake for tests and demos. Both pass
one contract suite.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Constructed by
    the Entrance's composition root or a test; used by
    ``hivemind.entrance.push.dispatch.PushDispatcher``. Calls into ``hivemind.common`` (SQLite,
    migrations) and the push records.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A delivery log row never outlives its subscription.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the one-file decision.
    - packages/hivemind/tests/contracts/test_push_subscription_store_contract.py for the contract.

Public API:
    - SubscriptionStore: the protocol.
    - SqliteSubscriptionStore, apply_push_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      store and its migration series.
    - MemorySubscriptionStore: the in-process store for tests and demos.
"""

from hivemind.entrance.push.store.memory import MemorySubscriptionStore
from hivemind.entrance.push.store.protocol import SubscriptionStore
from hivemind.entrance.push.store.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteSubscriptionStore,
    apply_push_migrations,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "MemorySubscriptionStore",
    "SqliteSubscriptionStore",
    "SubscriptionStore",
    "apply_push_migrations",
]
