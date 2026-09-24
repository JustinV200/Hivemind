"""Hold the SQLite schema and numbered migrations for the Honey Store's tables.

Kept separate from ripening and retrieval logic so the on-disk shape can be reviewed on its own.
`migrations/0001_create_honey_store.sql` creates the five tables of record (`honey_nectar`,
`honey`, `honey_vectors`, `honey_watermarks`, `honey_proposals`), the external-content FTS5 index
over `honey(title, summary, body)` and its three sync triggers (ADR-0031); `migrate.py` applies
that series the same way every other subsystem applies its own.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package.
    Handles the SQLite schema and migrations the rest of honey_store reads and writes through.
    Called by `hivemind.honey_store.store.sqlite.SqliteHoneyStore.create`; calls into
    `hivemind.common.migrations` and `hivemind.common.sqlite` only.

Key invariants:
    - Every `.sql` file under `migrations/` is numbered contiguously from 0001 with no gaps
      (`hivemind.common.migrations.load_migrations` enforces this at load time).

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under honey_store.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the schema this series creates.
    - hivemind.honey_store.store.sqlite for the one caller of `apply_honey_store_migrations`.

Public API:
    - apply_honey_store_migrations, MIGRATIONS_PACKAGE, SUBSYSTEM (migrate): apply this
      subsystem's numbered migration series to an open connection.
"""

from hivemind.honey_store.schema.migrate import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    apply_honey_store_migrations,
)

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "apply_honey_store_migrations"]
