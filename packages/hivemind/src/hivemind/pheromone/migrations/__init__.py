"""Hold the Pheromone Trail's numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching `NNNN_name.sql` (`hivemind.common.migrations.
MIGRATION_FILE_PATTERN`) is one migration in the Pheromone Trail's schema series. `hivemind.
pheromone.sqlite.apply_pheromone_migrations` reads them through `importlib.resources.files(
"hivemind.pheromone.migrations")`, which is exactly why this needs to be a real Python package
(an `__init__.py`, however empty) rather than a bare directory: `importlib.resources` addresses
packages by dotted name, not by filesystem path, so the files ship inside the installed
distribution the same way whether the Hive runs from a checkout or a wheel.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Read by hivemind.pheromone.sqlite; nothing
    imports this module for a Python name.

Key invariants:
    - Every `.sql` file here is numbered contiguously from 0001 with no gaps or duplicates
      (`hivemind.common.migrations.load_migrations` enforces this at load time, not this module).
    - No migration file's own text opens or closes a transaction (no `BEGIN`, no `COMMIT`):
      `hivemind.common.migrations._apply_one` supplies that wrapper around each file's text.

See Also:
    - hivemind.common.migrations for load_migrations/apply_migrations, which read this directory.
    - hivemind.pheromone.sqlite for apply_pheromone_migrations, the one caller.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the numbered-migration-series decision.

Public API: none; this package holds data files (`.sql` migrations), not importable names.
"""

__all__: list[str] = []
