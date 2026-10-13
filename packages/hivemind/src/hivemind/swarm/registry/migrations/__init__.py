"""Hold the swarm registry's numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching `NNNN_name.sql` (`hivemind.common.migrations.
MIGRATION_FILE_PATTERN`) is one migration in the registry's schema series, subsystem "swarm".
`hivemind.swarm.registry.sqlite.apply_swarm_migrations` reads them through
`importlib.resources.files("hivemind.swarm.registry.migrations")`, which is exactly why this needs
to be a real Python package (an `__init__.py`, however empty) rather than a bare directory:
`importlib.resources` addresses packages by dotted name, so the files ship inside the installed
distribution the same way whether the Hive runs from a checkout or a wheel.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Read by
    hivemind.swarm.registry.sqlite; nothing imports this module for a Python name.

Key invariants:
    - Every `.sql` file here is numbered contiguously from 0001 with no gaps or duplicates
      (`hivemind.common.migrations.load_migrations` enforces this at load time).
    - No migration file's own text opens or closes a transaction: `hivemind.common.migrations`
      supplies that wrapper around each file's text.

See Also:
    - hivemind.common.migrations for load_migrations/apply_migrations, which read this directory.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the numbered-migration-series decision.

Public API: none; this package holds data files (`.sql` migrations), not importable names.
"""

__all__: list[str] = []
