"""Tests for hivemind.honey_store.schema.migrate: the honey_store migration series.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/schema/migrate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.schema.migrate for the module under test.
"""

from __future__ import annotations

import importlib.resources

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import connect
from hivemind.honey_store.schema.migrate import MIGRATIONS_PACKAGE, SUBSYSTEM
from hivemind.pheromone import SqlitePheromoneTrail
from waggle.clock import FakeClock

# One 0001-shaped honey_nectar row, written in the exact column order that migration names.
_INSERT_0001_ROW_SQL = """
INSERT INTO honey_nectar (
    id, sha256, kind, origin, media_type, title, content, size_bytes, task_id, cell_id, bee,
    observed_at, received_at, clearance, clearance_rank, origin_tier, scope, state,
    ripen_attempts, tainted, source_key, event_id, ephemeral_cell_id
) VALUES (
    'nectar_pre0002', 'abc123', 'FINDING', 'BEE', 'text/plain', 'pre-existing', x'00', 1, NULL,
    'cell_pre0002', NULL, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'C1', 1,
    'MEADOW', 'hive', 'RECEIVED', 0, 0, NULL, NULL, NULL
)
"""


async def test_0002_applies_cleanly_onto_a_0001_only_store_with_rows_in_it() -> None:
    clock = FakeClock()
    connection = connect(":memory:")
    # honey_store's own migrations refuse without pheromone_events already present (SqliteHoneyStore
    # .create's own guard); applying the trail's series first mirrors every real composition root.
    await SqlitePheromoneTrail.create(connection, clock)
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    only_0001 = [migration for migration in migrations if migration.version == 1]
    apply_migrations(connection, SUBSYSTEM, only_0001, clock)
    # A row from before 0002 ever existed, in the shape 0001 alone created.
    connection.execute(_INSERT_0001_ROW_SQL)
    up_to_0002 = [migration for migration in migrations if migration.version <= 2]

    applied = apply_migrations(connection, SUBSYSTEM, up_to_0002, clock)

    assert applied == (2,)  # Only the new version ran; 0001 was already recorded.
    row = connection.execute(
        "SELECT title FROM honey_nectar WHERE id = 'nectar_pre0002'"
    ).fetchone()
    assert row["title"] == "pre-existing"  # The pre-existing row survived the upgrade untouched.
    assert connection.execute("SELECT * FROM honey_nectar_sources").fetchall() == []


async def test_0003_applies_onto_a_0002_store_and_leaves_old_rows_without_any_lowering_fact() -> (
    None
):
    clock = FakeClock()
    connection = connect(":memory:")
    await SqlitePheromoneTrail.create(connection, clock)
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    apply_migrations(connection, SUBSYSTEM, [m for m in migrations if m.version <= 2], clock)
    # A row written before ADR-0034: none of its three labelling facts was ever recorded.
    connection.execute(_INSERT_0001_ROW_SQL)

    applied = apply_migrations(connection, SUBSYSTEM, migrations, clock)

    assert applied == (3,)
    row = connection.execute(
        "SELECT declared_clearance, floor_clearance, ripener_clearance, ripener_reason, "
        "declared_clearance_rank FROM honey_nectar WHERE id = 'nectar_pre0002'"
    ).fetchone()
    assert tuple(row) == (None, None, None, None, None)  # Unknown, never guessed.
    assert connection.execute("SELECT * FROM honey_lowerings").fetchall() == []
