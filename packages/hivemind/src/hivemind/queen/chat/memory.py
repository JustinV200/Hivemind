"""Provide InMemoryChatLog, the in-process ChatLog for tests and demos.

One list of lines in append order (a line's `seq` is its position in it, from 1) and one index by
id, behind one lock: no SQL, no file, gone when the process exits. It implements
`hivemind.queen.chat.protocol.ChatLog` exactly as the durable `hivemind.queen.chat.sqlite.
SqliteChatLog` does, which the contract suite (`tests/contracts/test_chat_log_contract.py`)
proves, so a unit test can drive the whole chat path without a database file.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package. Built by `tests.builders.queen.make_queen_deps` and any composition root that
    wants no file. Calls into `hivemind.pheromone` (PheromoneTrail, QueenEvent) and the chat
    package's own model and protocol only.

Key invariants:
    - An append checks everything first, records its event (when given), and only then adds the
      line: a failed record leaves the log exactly as it was.
    - Every method holds the lock for its whole body, so `seq` is assigned without a race.

See Also:
    - hivemind.queen.chat.protocol for the contract this class implements.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from hivemind.pheromone import PheromoneTrail, QueenEvent
from hivemind.queen.chat.model import ChatAuthor, ChatEntry
from hivemind.queen.chat.protocol import ChatEntryExistsError, ChatQuery, check_chat_append

__all__ = ["InMemoryChatLog"]


class InMemoryChatLog:
    """An in-process ChatLog: lines in append order plus an index by id, guarded by one lock."""

    def __init__(self, trail: PheromoneTrail) -> None:
        """Create an empty log over `trail`.

        Args:
            trail: Where an append's event is recorded before the line is added.
        """
        self._trail = trail
        self._lines: list[ChatEntry] = []
        self._index: dict[str, int] = {}  # Line id -> its position in _lines (seq - 1).
        # Guards both collections: seq is len(_lines) + 1, so two appends must never interleave.
        self._lock = asyncio.Lock()

    async def append(self, entry: ChatEntry, event: QueenEvent | None = None) -> ChatEntry:
        """Append a fresh line (and its event); see ChatLog.append."""
        check_chat_append(entry, event)
        async with self._lock:
            if entry.id in self._index:
                raise ChatEntryExistsError(entry.id)
            if event is not None:
                await self._trail.record(event)
            stored = entry.model_copy(update={"seq": len(self._lines) + 1})
            self._index[stored.id] = len(self._lines)
            self._lines.append(stored)
        return stored

    async def read(self, query: ChatQuery) -> tuple[ChatEntry, ...]:
        """Return a page, oldest first; see ChatLog.read."""
        async with self._lock:
            matching = [line for line in self._lines if _matches(line, query)]
        # Anchored after a cursor: the oldest `limit` from there; otherwise the newest `limit`.
        if query.after_seq is not None:
            return tuple(matching[: query.limit])
        return tuple(matching[-query.limit :])

    async def unhandled(self, limit: int) -> tuple[ChatEntry, ...]:
        """Return waiting human messages, oldest first; see ChatLog.unhandled."""
        async with self._lock:
            waiting = [
                line
                for line in self._lines
                if line.author is ChatAuthor.HUMAN and line.handled_at is None
            ]
        return tuple(waiting[:limit])

    async def mark_handled(self, entry_id: str, handled_at: datetime) -> None:
        """Stamp a human message handled, idempotently; see ChatLog.mark_handled."""
        async with self._lock:
            position = self._index.get(entry_id)
            if position is None:
                return  # Unknown id: nothing to stamp (the contract's idempotent no-op).
            line = self._lines[position]
            if line.author is not ChatAuthor.HUMAN or line.handled_at is not None:
                return  # A Queen line is never handled; a handled one keeps its first stamp.
            self._lines[position] = line.model_copy(update={"handled_at": handled_at})


def _matches(line: ChatEntry, query: ChatQuery) -> bool:
    """Return whether `line` satisfies every filter `query` has set."""
    seq = line.seq if line.seq is not None else 0  # Always set once stored; 0 only for typing.
    if query.after_seq is not None and seq <= query.after_seq:
        return False
    if query.before_seq is not None and seq >= query.before_seq:
        return False
    return not (query.since is not None and line.at < query.since)
