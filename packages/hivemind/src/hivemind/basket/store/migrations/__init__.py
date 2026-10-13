"""Hold the Basket's numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching `NNNN_name.sql`
(`hivemind.common.migrations.MIGRATION_FILE_PATTERN`) is one migration in the Basket's schema
series. `hivemind.basket.store.migrate.apply_basket_migrations` reads them through
`importlib.resources.files("hivemind.basket.store.migrations")`, which is why this is a real Python
package: the files ship inside the installed distribution the same way from a checkout or a wheel.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by
    hivemind.basket.store.migrate; nothing imports this module for a Python name.

Key invariants:
    - Every `.sql` file here is numbered contiguously from 0001 with no gaps or duplicates
      (`hivemind.common.migrations.load_migrations` enforces this at load time).
    - No migration opens or closes a transaction: the shared runner wraps each file.

See Also:
    - hivemind.honey_store.schema.migrations for the pattern this mirrors.
"""
