"""Define RecordingStore and InMemoryRecordingStore: where flight recordings are kept.

A flight recording (roadmap step 6.6) is a header (`RecordingInfo`, one per attach) and the
`RecordedAction`s made under it, frames included. Until the Honey Store's Nectar intake exists
(phase 7), the store is the recording's Nectar body (ADR-0032): the SQLite implementation keeps it
in two tables of the Hive's own database file, and a Bee Bread entry references it. This module is
the protocol every implementation follows and the in-memory one tests, fakes and the Wardens of
Cells without a database use; the contract suite holds both to the same behaviour. A Warden that
lives for months (a colonized device's) would otherwise keep the frames of every GUI task it ever
ran, so the in-memory store may be given a `Retention`, and then forgets a stale recording as the
next one opens.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.recorder`. Written by `recorder.FlightRecorder`; read by the judge
    review, the playback export and the Observation Hive's read side. Calls into
    `hivemind.exoskeleton.errors` and `recorder.models` only.

Key invariants:
    - Actions come back in the order they were added.
    - `purge_cell` removes every recording of that Cell and everything under it (a Night Veil
      Cell's recordings go with the Cell, ADR-0032).
    - `prune_before` never removes a recording with an action finished at or after the cutoff.
    - A `Retention` only ever does what `prune_before` does, at the moment a recording opens, so
      a recording being written (it has an action newer than the window) is never forgotten.
    - Owns mutable state (codingrules 8.5, InMemoryRecordingStore): the two dicts, changed only
      through the protocol methods.

See Also:
    - hivemind.exoskeleton.recorder.sqlite for the SQLite store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from hivemind.exoskeleton.errors import RecordingNotFoundError
from hivemind.exoskeleton.recorder.models import RecordedAction, RecordingInfo
from waggle.clock import Clock

DEFAULT_LIST_LIMIT = 50  # Recordings listed at once, newest first.

__all__ = ["DEFAULT_LIST_LIMIT", "InMemoryRecordingStore", "RecordingStore", "Retention"]


class RecordingStore(Protocol):
    """Keep flight recordings: open one, add actions to it, read it back, purge a Cell's."""

    async def open(self, info: RecordingInfo) -> None:
        """Start a recording. Latency: one write. Opening an id twice is a no-op."""
        ...

    async def add(self, recording_id: str, action: RecordedAction) -> None:
        """Append one action to an open recording.

        Raises:
            RecordingNotFoundError: No recording has that id.
        """
        ...

    async def info(self, recording_id: str) -> RecordingInfo:
        """Return a recording's header.

        Raises:
            RecordingNotFoundError: No recording has that id.
        """
        ...

    async def actions(self, recording_id: str) -> tuple[RecordedAction, ...]:
        """Return a recording's actions in the order they were added.

        Raises:
            RecordingNotFoundError: No recording has that id.
        """
        ...

    async def recordings(
        self, cell_id: str | None = None, limit: int = DEFAULT_LIST_LIMIT
    ) -> tuple[RecordingInfo, ...]:
        """Return recording headers, newest first, optionally only one Cell's."""
        ...

    async def purge_cell(self, cell_id: str) -> int:
        """Delete every recording of `cell_id`, actions and frames included; return how many."""
        ...

    async def prune_before(self, cutoff: datetime) -> int:
        """Delete every recording with nothing in it at or after `cutoff`; return how many.

        The retention sweep: a recording goes once it started before `cutoff` and none of its
        actions finished at or after it, so one still being written is never removed.
        """
        ...


@dataclass(frozen=True, slots=True)
class Retention:
    """How long an in-memory store keeps a recording that nothing has been added to."""

    clock: Clock  # Supplies "now" for the sweep that runs as each recording opens.
    window: timedelta  # A recording with nothing in it newer than this is forgotten.


class InMemoryRecordingStore:
    """A RecordingStore in two dicts: for tests, fakes and a Hive with no database."""

    def __init__(self, retention: Retention | None = None) -> None:
        """Build an empty store.

        Args:
            retention: When given, each `open` first forgets the recordings `prune_before` would
                at `retention.window` before now, so a Warden that runs for months holds the
                last window's recordings and not every one it ever made. None keeps them all,
                which is right for a store that lives as long as one test or one Cell.
        """
        self._infos: dict[str, RecordingInfo] = {}
        self._actions: dict[str, list[RecordedAction]] = {}
        self._retention = retention

    async def open(self, info: RecordingInfo) -> None:
        """Start a recording, after forgetting the stale ones if a retention was given."""
        if self._retention is not None:
            # Swept as a recording opens, not on a timer: an idle Warden does no work, and a busy
            # one is bounded by its window. The recording about to open is newer than the cutoff.
            await self.prune_before(self._retention.clock.now() - self._retention.window)
        self._infos.setdefault(info.recording_id, info)
        self._actions.setdefault(info.recording_id, [])

    async def add(self, recording_id: str, action: RecordedAction) -> None:
        """Append one action; see RecordingStore."""
        self._require(recording_id)
        self._actions[recording_id].append(action)

    async def info(self, recording_id: str) -> RecordingInfo:
        """Return a header; see RecordingStore."""
        self._require(recording_id)
        return self._infos[recording_id]

    async def actions(self, recording_id: str) -> tuple[RecordedAction, ...]:
        """Return the actions in order; see RecordingStore."""
        self._require(recording_id)
        return tuple(self._actions[recording_id])

    async def recordings(
        self, cell_id: str | None = None, limit: int = DEFAULT_LIST_LIMIT
    ) -> tuple[RecordingInfo, ...]:
        """Return headers, newest first; see RecordingStore."""
        chosen = [i for i in self._infos.values() if cell_id is None or i.cell_id == cell_id]
        chosen.sort(key=lambda info: info.started_at, reverse=True)
        return tuple(chosen[:limit])

    async def purge_cell(self, cell_id: str) -> int:
        """Delete a Cell's recordings; see RecordingStore."""
        doomed = [rid for rid, info in self._infos.items() if info.cell_id == cell_id]
        for recording_id in doomed:
            del self._infos[recording_id]
            del self._actions[recording_id]
        return len(doomed)

    async def prune_before(self, cutoff: datetime) -> int:
        """Delete recordings with nothing at or after `cutoff`; see RecordingStore."""
        if cutoff.tzinfo is None:
            # The same refusal the SQLite store makes: a naive moment cannot be compared.
            raise ValueError("prune_before needs a timezone-aware cutoff")
        doomed = [
            recording_id
            for recording_id, info in self._infos.items()
            if info.started_at < cutoff
            and all(action.finished_at < cutoff for action in self._actions[recording_id])
        ]
        for recording_id in doomed:
            del self._infos[recording_id]
            del self._actions[recording_id]
        return len(doomed)

    def _require(self, recording_id: str) -> None:
        """Raise RecordingNotFoundError for an id this store never opened."""
        if recording_id not in self._infos:
            raise RecordingNotFoundError(recording_id)
