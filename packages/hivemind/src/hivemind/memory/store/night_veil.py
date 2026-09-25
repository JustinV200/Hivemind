"""Purge the in-memory store of a Night Veil Cell: the memory tables' one exception to append-only.

Codingrules section 12: nothing of a Night Veil Cell's work may outlive the Cell, and the Queen's
memory tables hold rows about it that no trail boundary can veil, since the store writes them
itself: an episode record of her own decision about one of its tasks (its trigger names the task,
the Cell or its Warden), a Handoff or Bee Bread entry filed under one of its tasks, a note naming
it, a Cell Wax row about the Cell. The teardown purge (`hivemind.pheromone.retention.purge`) hands
the memory side channel the Cell's id and every id that belongs to it; `purge_night_veil` removes
every row whose key names one of them (an episode's principal, a note's author, a Handoff's or
entry's task, a Cell Wax row's Cell) or whose stored body does. A row's taint label lives in the
row, so it goes with it. Pins stay: a pin is the human's own, kept on purpose. This is
`InMemoryMemoryStore`'s half, a mixin split out of `hivemind.memory.store.memory` for its line
budget; `hivemind.memory.store.sqlite.night_veil` is the SQLite half, and the contract suite holds
both to the same result.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `hivemind.memory.store.
    memory.InMemoryMemoryStore`. Calls into `hivemind.memory` (bee_bread.entry, cell_wax,
    episodes, handoff, notes) and waggle only.

Key invariants:
    - `purge_night_veil` is the only method here and removes rows only; it records no event (the
      purge's own `cell.purged` counts what went) and runs under the store's one lock.
    - An id names a row only as a whole key or inside its stored JSON: every id is a prefixed ULID,
      so a match inside a body is that id and no other.

See Also:
    - .claude/codingrules.md section 12 for the boundary this ends.
    - hivemind.memory.store.protocol for `MemoryStore.purge_night_veil`'s own contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, MutableMapping
from datetime import datetime

from pydantic import BaseModel

from hivemind.memory.bee_bread.entry import BeeBreadEntry
from hivemind.memory.cell_wax import CellWax
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import Note
from waggle.ids import TaskId

__all__ = ["names_any"]


def names_any(ids: frozenset[str], key: str | None, body: str) -> bool:
    """Return whether a row names one of `ids`: as its key, or anywhere in its stored JSON.

    Args:
        ids: The Cell's id and every id that belongs to it.
        key: The row's key column (a principal, author, task or Cell); None when unset.
        body: The row as its store holds it, as JSON.

    Returns:
        True when the row names the Cell or one of its members.
    """
    return key in ids or any(member in body for member in ids)


class _NightVeilMemoryStore:
    """The Night Veil purge's quarter of InMemoryMemoryStore, split out for codingrules 5.1.

    Reads the store's own dicts and lock, set by `InMemoryMemoryStore.__init__`; never
    instantiated on its own. The annotations below declare that shared state for mypy.
    """

    _notes: dict[str, Note]
    _handoffs: dict[str, tuple[Handoff, TaskId | None, datetime]]
    _episodes: dict[str, EpisodeRecord]
    _bee_bread: dict[str, BeeBreadEntry]
    _wax: dict[str, CellWax]
    _lock: asyncio.Lock

    async def purge_night_veil(self, ids: frozenset[str]) -> int:
        """Remove every row naming one of `ids`; see `MemoryStore.purge_night_veil`."""
        async with self._lock:
            # The same key column and body each table's SQLite statement matches on.
            return (
                _drop(self._episodes, lambda row: (row.principal, _json(row)), ids)
                + _drop(self._handoffs, lambda row: (row[1], _json(row[0])), ids)
                + _drop(self._bee_bread, lambda row: (row.task_id, _json(row)), ids)
                + _drop(self._notes, lambda row: (row.author, _json(row)), ids)
                + _drop(self._wax, lambda row: (row.cell_id, _json(row)), ids)
            )


def _drop[Row](
    table: MutableMapping[str, Row],
    key_and_body: Callable[[Row], tuple[str | None, str]],
    ids: frozenset[str],
) -> int:
    """Remove every row of `table` that names one of `ids`; return how many went."""
    doomed = [key for key, row in table.items() if names_any(ids, *key_and_body(row))]
    for key in doomed:
        del table[key]
    return len(doomed)


def _json(row: BaseModel) -> str:
    """Return `row` as the JSON a durable store holds for it."""
    return row.model_dump_json()
