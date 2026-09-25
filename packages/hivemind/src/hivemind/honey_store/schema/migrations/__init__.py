"""Hold the Honey Store's numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching `NNNN_name.sql`
(`hivemind.common.migrations.MIGRATION_FILE_PATTERN`) is one migration in this subsystem's schema
series. `hivemind.honey_store.schema.migrate.apply_honey_store_migrations` reads them through
`importlib.resources.files("hivemind.honey_store.schema.migrations")`, which is exactly why this
needs to be a real Python package (an `__init__.py`, however empty) rather than a bare directory:
`importlib.resources` addresses packages by dotted name, not by filesystem path, so the files ship
inside the installed distribution the same way whether the Hive runs from a checkout or a wheel.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by
    hivemind.honey_store.schema.migrate; nothing imports this module for a Python name.

Key invariants:
    - Every `.sql` file here is numbered contiguously from 0001 with no gaps or duplicates
      (`hivemind.common.migrations.load_migrations` enforces this at load time, not this module).
    - No migration file's own text opens or closes a transaction (no `BEGIN`, no `COMMIT`):
      `hivemind.common.migrations._apply_one` supplies that wrapper around each file's text.

See Also:
    - hivemind.common.migrations for load_migrations/apply_migrations, which read this directory.
    - hivemind.honey_store.schema.migrate for apply_honey_store_migrations, the one caller.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the schema this series creates.

Public API: none; this package holds data files (`.sql` migrations), not importable names.
"""

# Appendix A.3: nothing is re-exported; this package holds data files, not importable names.
__all__: list[str] = []
