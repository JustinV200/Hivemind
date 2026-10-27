"""Define RequeeningStore: where the Queen keeps what Requeening needs and nothing else holds.

Three small things outlive the Queen's process only if she writes them down (ADR-0062): her own
snapshot (the newest one; recovery folds the trail from its cursor), the Alarms and questions she
handled from Wardens (so a frame sent again after her restart is dropped, and a question's answer
can still be forwarded), and the port her Virtual Cell listener bound last (so a Cell redialing
her finds her again when the manifest lets the OS choose a port). `RequeeningStore` is the seam;
`MemoryRequeeningStore` (`.memory`) serves a test or a Queen with no database file, and
`SqliteRequeeningStore` (`.sqlite`) the Hive's own `[hive] db`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.
    requeening.store`. Held by `hivemind.queen.requeening.book.RequeeningBook`; written by the
    Queen's tick (`.snapshot`, `.handled`) and the Cell listener's port memory; read once by
    `.recover` at her start. Calls into `hivemind.queen.requeening.models` only.

Key invariants:
    - Only the newest snapshot is kept: `put_snapshot` replaces the one before.
    - `note_handled` never loses a question's wiring: a plain row for an envelope already noted
      with its wiring changes nothing, and a row with wiring replaces a plain one.
    - Every method is safe to call concurrently from one event loop.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for what each record is for.
    - hivemind.queen.requeening.models for the rows themselves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.queen.requeening.models import HandledEnvelope, QueenSnapshot

__all__ = ["RequeeningStore"]


class RequeeningStore(Protocol):
    """Persist the Queen's snapshot, her handled envelopes and her listener's last port."""

    async def put_snapshot(self, snapshot: QueenSnapshot) -> None:
        """Write `snapshot` as the newest, replacing whatever was kept before.

        Args:
            snapshot: The snapshot the Queen's tick just built.
        """
        ...

    async def latest_snapshot(self) -> QueenSnapshot | None:
        """Return the newest snapshot, or None when none was ever written."""
        ...

    async def note_handled(self, row: HandledEnvelope) -> None:
        """Record that the Queen handled `row`'s envelope; see the module's wiring invariant.

        Args:
            row: The sender, the envelope id, when, and a question's wiring if it has one.
        """
        ...

    async def handled_since(self, since: datetime) -> tuple[HandledEnvelope, ...]:
        """Return every row handled at or after `since`, oldest first.

        Args:
            since: The earliest instant still inside the transport's replay window.
        """
        ...

    async def prune_handled(self, before: datetime) -> int:
        """Forget every row handled before `before`; nothing older is ever sent again.

        Args:
            before: The start of the replay window.

        Returns:
            How many rows were forgotten.
        """
        ...

    async def recall_port(self, name: str) -> int | None:
        """Return the port the listener `name` bound last, or None when it never bound one.

        Args:
            name: The listener's name, e.g. `virtual_cells`.
        """
        ...

    async def remember_port(self, name: str, port: int) -> None:
        """Record that the listener `name` bound `port`, replacing what was kept before.

        Args:
            name: The listener's name.
            port: The port it bound; 1 to 65535.
        """
        ...
