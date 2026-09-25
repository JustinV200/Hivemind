"""Persist the taint label in SQLite: the scope scan, one item's read, and the labelled write.

The memory tables are a `hivemind.memory.taint.TaintLedger` (roadmap step 10.6d). This module is
that ledger's SQLite half, split from `hivemind.memory.store.sqlite.store` for codingrules 5.1's
class-size limit, in the same shape `hivemind.memory.store.memory._TaintMemoryStore` gives the
in-memory store: `_SqliteTaintStore` is the mixin `SqliteMemoryStore` inherits, and the functions
below are the SQL it runs on the store's own connection thread. A taint scope's scan reads every
row written at or after the scope's moment that is not already tainted (an indexed time column plus
the `taint_state` column migration 0004 added), then applies `TaintScope.covers` to each decoded
row in Python: the one predicate both stores share, so they can never disagree about what a scope
reaches. The write checks the label's transition table against the stored label and then rewrites
the row's body, its `taint_state` and inserts the trail event, all inside one transaction.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `hivemind.memory.store.
    sqlite.store.SqliteMemoryStore`. Calls into `hivemind.common.sqlite` (ConnectionThread,
    transaction), `hivemind.memory` (bee_bread.entry, episodes, errors, handoff, store.review,
    taint) and `hivemind.pheromone` (insert_event) only.

Key invariants:
    - `write_taint_transaction` raises before writing anything when the edge is illegal or the row
      is missing; otherwise the body, `taint_state` and the event commit together.
    - The SQL names its tables literally (no string-built identifiers): each statement below is a
      constant, one per table.

See Also:
    - hivemind.memory.store.migrations 0004_add_taint_label.sql for the columns read here.
    - hivemind.memory.taint.ledger for the TaintLedger contract.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.memory.bee_bread.entry import BeeBreadEntry
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.errors import TaintTargetNotFoundError
from hivemind.memory.handoff import Handoff
from hivemind.memory.store.review import review_text
from hivemind.memory.taint import (
    TaintableItem,
    TaintedKind,
    TaintMarker,
    TaintScope,
    TaintTarget,
    assert_transition,
)
from hivemind.pheromone import MemoryEvent, insert_event

# No __all__: see hivemind.memory.store.sqlite.records's own note; the same reasoning applies here.

_Taintable = Handoff | EpisodeRecord | BeeBreadEntry


@dataclass(frozen=True, slots=True)
class _TableSql:
    """The three statements one taintable table needs, written out literally."""

    scan: str  # item_id, at, task_id and body of every untainted row written since a moment.
    select: str  # clearance and body of one row by id.
    update: str  # set taint_state and body of one row by id.


# One entry per kind the memory tables hold; NECTAR and HONEY live in the Honey Store (phase 7).
_SQL: Mapping[TaintedKind, _TableSql] = MappingProxyType(
    {
        TaintedKind.HANDOFF: _TableSql(
            scan="SELECT event_id AS item_id, written_at AS at, task_id, body FROM memory_handoffs "
            "WHERE written_at >= ? AND taint_state IS NOT 'tainted'",
            select="SELECT clearance, body FROM memory_handoffs WHERE event_id = ?",
            update="UPDATE memory_handoffs SET taint_state = ?, body = ? WHERE event_id = ?",
        ),
        TaintedKind.EPISODE: _TableSql(
            scan="SELECT id AS item_id, at, NULL AS task_id, body FROM memory_episodes "
            "WHERE at >= ? AND taint_state IS NOT 'tainted'",
            select="SELECT clearance, body FROM memory_episodes WHERE id = ?",
            update="UPDATE memory_episodes SET taint_state = ?, body = ? WHERE id = ?",
        ),
        TaintedKind.BEE_BREAD: _TableSql(
            scan="SELECT id AS item_id, created_at AS at, task_id, body FROM memory_bee_bread "
            "WHERE created_at >= ? AND taint_state IS NOT 'tainted'",
            select="SELECT clearance, body FROM memory_bee_bread WHERE id = ?",
            update="UPDATE memory_bee_bread SET taint_state = ?, body = ? WHERE id = ?",
        ),
    }
)


class _SqliteTaintStore:
    """The taint label's quarter of SqliteMemoryStore (roadmap 10.6d), split for codingrules 5.1.

    Reads `self._connection`, `self._thread` and `self._lock`, set by `SqliteMemoryStore.
    __init__`; never instantiated on its own. The annotations declare that shared state for mypy.
    """

    _connection: sqlite3.Connection
    _thread: ConnectionThread
    _lock: asyncio.Lock

    async def find_taintable(self, scope: TaintScope) -> tuple[TaintTarget, ...]:
        """Return what `scope` covers that is not TAINTED, oldest first; see `MemoryStore`."""
        async with self._lock:
            rows = await self._thread.run(_scan_rows, self._connection, scope)
        covered: list[tuple[datetime, TaintTarget]] = []
        # Each scanned row decoded once, kept only when the scope names its author or its task.
        for kind, row in rows:
            at = datetime.fromisoformat(row["at"])
            if scope.covers(kind, _author(_decode(kind, row["body"])), row["task_id"], at):
                covered.append((at, TaintTarget(kind=kind, item_id=row["item_id"])))
        covered.sort(key=lambda pair: (pair[0], pair[1].item_id))
        return tuple(target for _at, target in covered)

    async def read_taintable(self, target: TaintTarget) -> TaintableItem:
        """Return one item's label, clearance and review text; see `MemoryStore.read_taintable`."""
        async with self._lock:
            row = await self._thread.run(_select_row, self._connection, target)
        item = _decode(target.kind, row["body"])
        return TaintableItem(
            target=target,
            marker=item.tainted,
            clearance=HoneyClearance(row["clearance"]),
            content=review_text(item),
        )

    async def write_taint(
        self, target: TaintTarget, marker: TaintMarker, event: MemoryEvent
    ) -> None:
        """Replace one item's label and record `event`; see `MemoryStore.write_taint`."""
        async with self._lock:
            await self._thread.run(
                _write_taint_transaction, self._connection, target, marker, event
            )


def _scan_rows(
    connection: sqlite3.Connection, scope: TaintScope
) -> list[tuple[TaintedKind, sqlite3.Row]]:
    """Select every untainted row written since `scope.since`, for each kind the scope reaches."""
    rows: list[tuple[TaintedKind, sqlite3.Row]] = []
    for kind, sql in _SQL.items():
        if kind in scope.kinds:
            rows += [
                (kind, row) for row in connection.execute(sql.scan, (scope.since.isoformat(),))
            ]
    return rows


def _select_row(connection: sqlite3.Connection, target: TaintTarget) -> sqlite3.Row:
    """Select one taintable row, or raise when the memory tables hold no such item."""
    sql = _SQL.get(target.kind)
    row: sqlite3.Row | None = (
        connection.execute(sql.select, (target.item_id,)).fetchone() if sql is not None else None
    )
    if row is None:
        raise TaintTargetNotFoundError(target.kind.value, target.item_id)
    return row


def _write_taint_transaction(
    connection: sqlite3.Connection, target: TaintTarget, marker: TaintMarker, event: MemoryEvent
) -> None:
    """Check the edge, rewrite the row's label and insert its event, in one transaction."""
    with transaction(connection):
        row = _select_row(connection, target)
        item = _decode(target.kind, row["body"])
        # The stored label, read inside this transaction, is the one the edge is checked against.
        before = item.tainted.state if item.tainted is not None else None
        assert_transition(before, marker.state, target.item_id)
        labelled = item.model_copy(update={"tainted": marker})
        connection.execute(
            _SQL[target.kind].update,
            (marker.state.value, labelled.model_dump_json(), target.item_id),
        )
        insert_event(connection, event)


def _decode(kind: TaintedKind, body: str) -> _Taintable:
    """Decode one row's body as the model its kind names."""
    if kind is TaintedKind.HANDOFF:
        return Handoff.model_validate_json(body)
    if kind is TaintedKind.EPISODE:
        return EpisodeRecord.model_validate_json(body)
    return BeeBreadEntry.model_validate_json(body)


def _author(item: _Taintable) -> str | None:
    """Return who wrote `item`: a Handoff's writer, an episode's principal; None for an entry."""
    if isinstance(item, Handoff):
        return item.written_by
    if isinstance(item, EpisodeRecord):
        return item.principal
    return None
