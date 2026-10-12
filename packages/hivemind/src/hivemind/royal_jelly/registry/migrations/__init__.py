"""Hold the Comb Registry's numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching `NNNN_name.sql` (`hivemind.common.migrations.
MIGRATION_FILE_PATTERN`) is one migration in the Comb Registry's schema series.
`hivemind.royal_jelly.registry.sqlite.apply_comb_registry_migrations` reads them through
`importlib.resources.files("hivemind.royal_jelly.registry.migrations")`, which is why this is a
real Python package: `importlib.resources` addresses packages by dotted name, so the files ship
inside the installed distribution the same way from a checkout or a wheel.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry. Read by
    hivemind.royal_jelly.registry.sqlite; nothing imports this module for a Python name.

Key invariants:
    - Every `.sql` file here is numbered contiguously from 0001 with no gaps or duplicates
      (`hivemind.common.migrations.load_migrations` enforces this at load time).
    - No migration file's own text opens or closes a transaction: `hivemind.common.migrations`
      supplies that wrapper around each file's text.

See Also:
    - hivemind.cell.leavings.migrations for the same package shape.
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the table this series creates.
"""
