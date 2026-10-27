"""Define MemoryRequeeningStore: the RequeeningStore that keeps everything in this process.

The non-durable `RequeeningStore`: what a test builds directly, and what a Queen with no database
file holds by default (`hivemind.queen.requeening.book.RequeeningBook`). It forgets everything
when the process ends, which is exactly the case Requeening cannot help: a restarted Queen with
it starts from nothing, as before ADR-0062.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.
    requeening.store`. Implements `.protocol.RequeeningStore`. Calls into `hivemind.queen.
    requeening.models` only.

Key invariants:
    - Behaves exactly as `SqliteRequeeningStore` does for every method, the wiring rule included
      (a contract test runs both).

See Also:
    - hivemind.queen.requeening.store.sqlite for the durable implementation.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.queen.requeening.models import HandledEnvelope, QueenSnapshot

__all__ = ["MemoryRequeeningStore"]


class MemoryRequeeningStore:
    """Keep the snapshot, the handled rows and the ports in plain fields (no lock: no awaits)."""

    def __init__(self) -> None:
        """Build an empty store: no snapshot, nothing handled, no port remembered."""
        self._snapshot: QueenSnapshot | None = None
        self._handled: dict[tuple[str, str], HandledEnvelope] = {}
        self._ports: dict[str, int] = {}

    async def put_snapshot(self, snapshot: QueenSnapshot) -> None:
        """Keep `snapshot` as the newest; see `RequeeningStore.put_snapshot`."""
        self._snapshot = snapshot

    async def latest_snapshot(self) -> QueenSnapshot | None:
        """Return the newest snapshot; see `RequeeningStore.latest_snapshot`."""
        return self._snapshot

    async def note_handled(self, row: HandledEnvelope) -> None:
        """Record `row`, keeping a question's wiring; see `RequeeningStore.note_handled`."""
        key = (row.principal, row.envelope_id)
        known = self._handled.get(key)
        # A plain note after the wiring was written (the tick notes every handled item, after the
        # question path wrote its wiring) must not wipe the wiring out.
        if known is not None and row.question is None:
            return
        self._handled[key] = row

    async def handled_since(self, since: datetime) -> tuple[HandledEnvelope, ...]:
        """Return the rows handled at or after `since`; see `RequeeningStore.handled_since`."""
        young = (row for row in self._handled.values() if row.handled_at >= since)
        return tuple(sorted(young, key=lambda row: (row.handled_at, row.envelope_id)))

    async def prune_handled(self, before: datetime) -> int:
        """Forget rows handled before `before`; see `RequeeningStore.prune_handled`."""
        old = [key for key, row in self._handled.items() if row.handled_at < before]
        for key in old:
            del self._handled[key]
        return len(old)

    async def recall_port(self, name: str) -> int | None:
        """Return the port `name` bound last; see `RequeeningStore.recall_port`."""
        return self._ports.get(name)

    async def remember_port(self, name: str, port: int) -> None:
        """Keep `port` for `name`; see `RequeeningStore.remember_port`."""
        self._ports[name] = port
