"""Persist the Basket: the BasketStore protocol, its SQLite implementation and its small rows.

The protocol is deliberately small and inert: deposit, read (hash-verified), list, retire, track a
Cell's resident blobs and queue shipments. There is no method that runs, installs or loads a blob.
`SqliteBasketStore` keeps rows in the Hive's SQLite file and bytes in a `<sha[:2]>/<sha>` directory
written atomically (`files.BlobFiles`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Called by
    `hivemind.basket.deposits` and `residency`, and by composition roots that open the store.
    Calls into `hivemind.common` (sqlite, migrations) and `hivemind.basket.models`.

Key invariants:
    - Whatever is not in `__all__` is private to this package.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md.
    - hivemind.honey_store.store for the pattern this mirrors.

Public API:
    - BasketStore (protocol): the persistence protocol every implementation honours.
    - SqliteBasketStore (sqlite): the durable implementation (create applies the migrations).
    - ResidentBlob, Shipment (rows): the residency and shipment rows.
    - apply_basket_migrations (migrate): apply the numbered migration series to a connection.
"""

from hivemind.basket.store.migrate import apply_basket_migrations
from hivemind.basket.store.protocol import BasketStore
from hivemind.basket.store.rows import ResidentBlob, Shipment
from hivemind.basket.store.sqlite import SqliteBasketStore

__all__ = [
    "BasketStore",
    "ResidentBlob",
    "Shipment",
    "SqliteBasketStore",
    "apply_basket_migrations",
]
