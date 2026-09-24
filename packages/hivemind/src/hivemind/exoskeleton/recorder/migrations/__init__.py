"""Hold the flight recorder's numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching `NNNN_name.sql` (`hivemind.common.migrations.
MIGRATION_FILE_PATTERN`) is one migration in the flight recorder's schema series (the recordings of
every GUI action an Exoskeleton, a Cell's optional display, input, audio and browser, carried out).
`hivemind.exoskeleton.recorder.sqlite.apply_recording_migrations` reads them through
`importlib.resources.files("hivemind.exoskeleton.recorder.migrations")`, which is why this is a
real Python package (an `__init__.py`, however empty) rather than a bare directory:
`importlib.resources` addresses packages by dotted name, so the files ship inside the installed
distribution the same way whether the Hive runs from a checkout or a wheel.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.recorder`. Read by `hivemind.exoskeleton.recorder.sqlite`; nothing
    imports this module for a Python name.

Key invariants:
    - Every `.sql` file here is numbered contiguously from 0001 with no gaps or duplicates
      (`hivemind.common.migrations.load_migrations` enforces this at load time, not this module).
    - No migration file's own text opens or closes a transaction (no `BEGIN`, no `COMMIT`):
      `hivemind.common.migrations._apply_one` supplies that wrapper around each file's text.

See Also:
    - hivemind.common.migrations for load_migrations/apply_migrations, which read this directory.
    - hivemind.exoskeleton.recorder.sqlite for apply_recording_migrations, the one caller.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for "two
      tables in the Hive's SQLite file".

Public API: none; this package holds data files (`.sql` migrations), not importable names.
"""

__all__: list[str] = []
