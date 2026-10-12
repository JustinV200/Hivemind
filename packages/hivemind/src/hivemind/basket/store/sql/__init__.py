"""Hold the Basket store's SQL: one module per table family, plain functions over a connection.

`blobs` reads and writes `basket_blobs` (rows, manifests, dedupe, retire), `residency` reads and
writes `basket_residency` and `shipments` reads and writes `basket_shipments`. Every function is
synchronous and runs on `SqliteBasketStore`'s connection thread; none is public outside
`hivemind.basket.store`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store`. Called
    by `hivemind.basket.store.sqlite` only.

Key invariants:
    - Nothing exported here is part of `hivemind.basket`'s public API.

See Also:
    - hivemind.basket.store.sqlite for the class that calls these functions.

Public API:
    - None: this package is private to `hivemind.basket.store`; import its modules directly.
"""
