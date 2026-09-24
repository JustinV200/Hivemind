"""SQL for `honey_nectar_sources`: extra provenance a content duplicate deposit records (ADR-0033).

`insert_source_if_new` is called only from inside `nectar.add_nectar_transaction`'s own open
transaction, when a duplicate found by content sha256 (not by `source_key`) carries provenance
that differs from the stored Nectar row's own; `INSERT OR IGNORE` against the migration's two
unique indexes (`source_key` alone, and the whole `(nectar, source_key, task, Cell, bee)` tuple)
makes a retried delivery or a duplicate whose provenance already matches a no-op, so the table
grows with distinct sources only. `select_has_source` is `HoneyStore.has_source`'s second table;
`select_sources_for_nectar` is `HoneyStore.nectar_sources`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.nectar` (the insert, inside its own transaction) and
    `.store` (the two reads), on the store's `ConnectionThread`. Calls into `hivemind.cell`,
    `hivemind.honey_store.models.nectar` and `waggle.ids` only.

Key invariants:
    - `insert_source_if_new` never opens its own transaction: it is always called from inside a
      caller's already-open one, matching every other per-table helper in this sub-package.
    - A row here never carries content: only ids, times, an enum value and a declared label
      (codingrules section 12), the same fields `honey_nectar` itself already stores per deposit.

See Also:
    - hivemind.honey_store.store.sqlite.nectar for add_nectar_transaction, the one caller that
      decides when a source is worth recording.
    - docs/adr/0033-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for the
      dedupe-and-record rule this module implements.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.models.nectar import NectarDraft, NectarOrigin, NectarSource
from waggle.ids import EventId, NectarId, TaskId

# No __all__: internal collaborator of hivemind.honey_store.store.sqlite.{nectar,store} only (see
# hivemind.honey_store.store.sqlite.nectar's identical note on this convention).

_INSERT_SOURCE_SQL = """
INSERT OR IGNORE INTO honey_nectar_sources (
    nectar_id, source_key, task_id, cell_id, bee, observed_at, received_at, origin, origin_tier,
    clearance, event_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_HAS_SOURCE_SQL = "SELECT 1 FROM honey_nectar_sources WHERE source_key = ?"
_SELECT_FOR_NECTAR_SQL = (
    "SELECT * FROM honey_nectar_sources WHERE nectar_id = ? ORDER BY received_at, id"
)


def insert_source_if_new(
    connection: sqlite3.Connection, nectar_id: NectarId, draft: NectarDraft, received_at: datetime
) -> None:
    """Record `draft` as an extra source of `nectar_id`, unless the same one is already known.

    Args:
        connection: The open connection, already inside the caller's transaction.
        nectar_id: The stored Nectar row this source deduplicated onto.
        draft: The duplicate deposit; its own provenance is what gets recorded, never its content.
        received_at: When this duplicate reached intake (the caller's own clock read, not
            `draft.observed_at`, which is when the underlying finding happened).
    """
    connection.execute(
        _INSERT_SOURCE_SQL,
        (
            nectar_id,
            draft.source_key,
            draft.task_id,
            draft.cell_id,
            draft.bee,
            draft.observed_at.isoformat(),
            received_at.isoformat(),
            draft.origin.value,
            draft.origin_tier.value,
            # What this source declared (ADR-0033), not its floor-raised label; a draft built
            # outside intake records no declared fact, so its own label stands in for one.
            (draft.declared_clearance or draft.clearance).value,
            draft.event_id,
        ),
    )


def select_has_source(connection: sqlite3.Connection, source_key: str) -> bool:
    """Return whether `source_key` is recorded on honey_nectar_sources."""
    return connection.execute(_SELECT_HAS_SOURCE_SQL, (source_key,)).fetchone() is not None


def select_sources_for_nectar(
    connection: sqlite3.Connection, nectar_id: NectarId
) -> tuple[NectarSource, ...]:
    """Return every extra source recorded for `nectar_id`, oldest first."""
    rows = connection.execute(_SELECT_FOR_NECTAR_SQL, (nectar_id,)).fetchall()
    return tuple(_row_to_source(row) for row in rows)


def _row_to_source(row: sqlite3.Row) -> NectarSource:
    """Decode one `honey_nectar_sources` row back into a NectarSource."""
    return NectarSource(
        nectar_id=row["nectar_id"],
        source_key=row["source_key"],
        task_id=_optional(TaskId, row["task_id"]),
        cell_id=row["cell_id"],
        bee=row["bee"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        origin=NectarOrigin(row["origin"]),
        origin_tier=CombShieldLevel(row["origin_tier"]),
        clearance=HoneyClearance(row["clearance"]),
        event_id=_optional(EventId, row["event_id"]),
    )


def _optional[IdT](wrapper: Callable[[str], IdT], value: str | None) -> IdT | None:
    """Apply `wrapper` to `value` unless it is None; a private copy of `nectar._optional`.

    Not imported from `.nectar` (whose own `_optional` exists for the identical reason): `nectar`
    imports this module for `insert_source_if_new`, so importing back would cycle. `wrapper` is
    typed as a plain callable, not `type[IdT]`, because a `waggle.ids` `NewType` (`TaskId`,
    `EventId`, ...) is a callable that returns its own alias, never an actual `type` object.
    """
    return None if value is None else wrapper(value)
