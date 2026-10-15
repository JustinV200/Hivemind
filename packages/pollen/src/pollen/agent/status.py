"""Keep the packet's own diagnostics: the status document ``pollen status`` reads.

The Pollen Packet (the thin gateway program on a device the Hive borrows) keeps no audit log of
its own; the Pheromone Trail (the Hive's append-only audit log) is written on the Hive Stand from
its side of the link (gateway-contract section 16). What it does keep is a small status document
for the operator, local diagnostics only: whether the link to the Queen (the Hive's central
orchestrator) is up and since when, the stable code of the last failure to connect, how many
frames were dropped and why, how many leases (the Hive's temporary holds on the device) are open,
how many frames wait in the outbox and how long the Queen has been silent. It also says when the
device's clock looks wrong: a gateway whose clock is more than a few minutes off drops every frame
the Queen sends as stale or from the future, and the contract asks it to notice and say so rather
than retry silently (section 7). The document holds ids, counts and error codes and nothing else:
no address, path, argument value or key.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Updated by
    pollen.agent.dispatch (drops), pollen.agent.packet (the link) and written by
    pollen.agent.heartbeat; read by pollen.cli for ``pollen status``; calls into pollen.state
    (the atomic writer) and waggle only.

Key invariants:
    - The document is replaced atomically, like every file the packet keeps, and is owner-only.
    - Nothing in it is a secret or a path: it is safe to print and to attach to a bug report.
    - The clock is suspect after a run of consecutive freshness drops with no admitted frame
      between, and clears itself the moment one frame is admitted.

See Also:
    - docs/waggle/gateway-contract.md section 7 (freshness) and section 16 (no audit log).
    - pollen.state for ``write_atomic`` and the data layout.
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pollen.executors.info import runtime_version
from pollen.state import when_free, write_atomic
from waggle.clock import Clock
from waggle.messages.base import UtcDatetime

CLOCK_SUSPECT_STREAK = 3  # This many freshness drops in a row, with nothing admitted between.
FRESHNESS_DROPS = frozenset({"STALE", "FUTURE"})  # The drop reasons that point at the clock.

__all__ = ["CLOCK_SUSPECT_STREAK", "PacketStatus", "StatusDocument", "read_status", "write_status"]


class StatusDocument(BaseModel):
    """What ``pollen status`` shows about a running packet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pid: int = Field(ge=1, description="The packet's process id, so a stale file is recognisable.")
    version: str = Field(description="The packet's version.")
    started_at: UtcDatetime = Field(description="When this packet process started.")
    updated_at: UtcDatetime = Field(description="When this document was written.")
    is_connected: bool = Field(description="Whether the link to the Queen is up right now.")
    connected_since: UtcDatetime | None = Field(
        description="When the current connection announced itself; None while down."
    )
    last_failure: str | None = Field(
        description="The stable error code of the last failed or lost connection, if any."
    )
    dropped: dict[str, int] = Field(description="Frames dropped without acting, by reason.")
    is_clock_suspect: bool = Field(
        description="True when the Queen's frames keep arriving stale or from the future."
    )
    open_leases: int = Field(ge=0, description="Leases currently held.")
    outbox_pending: int = Field(ge=0, description="Frames waiting for the next connection.")
    silence_s: float = Field(ge=0, description="Seconds since the Queen was last heard from.")


class PacketStatus:
    """Collects the counters the status document reports.

    Owns mutable state (codingrules 8.5, documented): the connection flags, the last failure code,
    the drop counters and the freshness streak; only the packet's own tasks touch it, all on one
    event loop.
    """

    def __init__(self, clock: Clock) -> None:
        """Start with no connection and no drops.

        Args:
            clock: Stamps the document's times.
        """
        self._clock = clock
        self._started_at = clock.now()
        self._connected_since: datetime | None = None
        self._is_connected = False
        self._last_failure: str | None = None
        self._dropped: Counter[str] = Counter()
        self._streak = 0  # Consecutive freshness drops since a frame was last admitted.

    @property
    def is_clock_suspect(self) -> bool:
        """Whether the device's clock looks wrong to the Queen's frames."""
        return self._streak >= CLOCK_SUSPECT_STREAK

    def connected(self) -> None:
        """Record that a connection announced itself."""
        self._is_connected = True
        self._connected_since = self._clock.now()
        self._last_failure = None

    def disconnected(self, failure: str | None) -> None:
        """Record that the connection ended or never came up.

        Args:
            failure: The stable code of what went wrong, or None for a clean end.
        """
        self._is_connected = False
        self._connected_since = None
        if failure is not None:
            self._last_failure = failure

    def note_drop(self, reason: str) -> None:
        """Count a frame that was dropped without acting.

        Args:
            reason: The drop reason's name (``DropReason.value``).
        """
        self._dropped[reason] += 1
        self._streak = self._streak + 1 if reason in FRESHNESS_DROPS else 0

    def note_admitted(self) -> None:
        """Record that a frame passed every admission rule."""
        self._streak = 0

    def snapshot(
        self, *, open_leases: int, outbox_pending: int, silence_s: float
    ) -> StatusDocument:
        """Build the document as of now.

        Args:
            open_leases: How many leases are held.
            outbox_pending: How many frames wait in the outbox.
            silence_s: Seconds since the Queen was last heard from.

        Returns:
            The document, ready to write.
        """
        return StatusDocument(
            pid=os.getpid(),
            version=runtime_version(),
            started_at=self._started_at,
            updated_at=self._clock.now(),
            is_connected=self._is_connected,
            connected_since=self._connected_since,
            last_failure=self._last_failure,
            dropped=dict(self._dropped),
            is_clock_suspect=self.is_clock_suspect,
            open_leases=open_leases,
            outbox_pending=outbox_pending,
            silence_s=silence_s,
        )


async def write_status(path: Path, document: StatusDocument) -> None:
    """Replace the status file with ``document``; a failure is logged by the caller, not fatal.

    Args:
        path: The status file (``DataLayout.status``).
        document: What to write.

    Raises:
        OSError: The file could not be written; the packet carries on without a status file.
    """
    data = document.model_dump_json(indent=2).encode("utf-8")
    await asyncio.to_thread(write_atomic, path, data)


def read_status(path: Path) -> StatusDocument | None:
    """Read the status file back.

    Args:
        path: The status file.

    Returns:
        The document, or None when there is none or it cannot be read (no packet has run here, or
        it was damaged; the file is diagnostics only, so neither is an error).
    """
    try:
        # Through `when_free`: on Windows the read is refused while the packet replaces the file.
        return StatusDocument.model_validate_json(when_free(path.read_bytes))
    except (OSError, ValidationError):
        return None
