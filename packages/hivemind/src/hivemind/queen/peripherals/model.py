"""Define WatchRequest: one task's ask to watch the owner on one Cell, and the owner's answer.

ADR-0069: a Worker drives the operator's running screen (`exoskeleton:real_display`) or the sound
server (`exoskeleton:audio`) only after the owner approved that exact task on that exact Cell from
the Hive Stand's browser. The dispatcher files a `WatchRequest` when it has placed such a task and
holds the task PENDING until the owner answers. The request names everything the owner needs to
decide: which scopes, which Cell, which task (its one-line title, never its objective), and which
bee would hold them (the Cell's Warden and the Worker role it would spawn). It is APPROVED or
DENIED once, by a device, or EXPIRED once `expires_at` has passed unanswered.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    peripherals sub-package. Built and settled by `hivemind.queen.peripherals.book`; read by the
    dispatcher's gate, the Queen's door and, through her door, the Hive Entrance. Calls into
    waggle (ids, WorkerRole) only.

Key invariants:
    - A request is settled at most once: only a PENDING one becomes APPROVED, DENIED or EXPIRED.
    - `scopes` is never empty and holds only `waggle.messages.task.OWNER_APPROVED_SCOPES` entries.

See Also:
    - docs/adr/0069-the-owner-approves-the-running-screen-and-audio-at-the-hive-stand.md.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from waggle.clock import Clock
from waggle.ids import CellId, DeviceId, TaskId, WardenId
from waggle.messages.task import WorkerRole
from waggle.ulid import encode_ulid

WATCH_ID_PREFIX = "watch_"  # Tells a watch request's id apart in a log line.
WATCH_ID_PATTERN = r"^watch_[0-9A-HJKMNP-TV-Z]{26}$"  # watch_ plus a Crockford ULID.
_ULID_RANDOM_BYTES = 10  # A ULID's 80 random bits.
_MILLISECONDS_PER_SECOND = 1_000

__all__ = [
    "WATCH_ID_PATTERN",
    "WATCH_ID_PREFIX",
    "WatchRequest",
    "WatchStatus",
    "new_watch_id",
]


class WatchStatus(Enum):
    """Where one watch request stands."""

    PENDING = "PENDING"  # Filed; the task waits for the owner.
    APPROVED = "APPROVED"  # The owner said yes at the Hive Stand: the task may be assigned.
    DENIED = "DENIED"  # The owner said no: the task is cancelled.
    EXPIRED = "EXPIRED"  # Nobody answered in time: the task is cancelled.


@dataclass(frozen=True, slots=True)
class WatchRequest:
    """One task's ask for the scopes that watch the owner, on one Cell.

    Attributes:
        id: `watch_` plus a ULID, so requests sort by when they were filed.
        task_id: The task that would hold the scopes.
        title: The task's one-line title, so the owner knows what it is for.
        cell_id: The Cell the task was placed on: the machine that would be watched.
        cell_name: That Cell's operator-facing name.
        warden_id: The Cell's Warden, which would spawn the Worker holding the scopes.
        role: The Worker role it would spawn.
        scopes: The owner-approved scopes asked for, sorted.
        requested_at: When the dispatcher filed it.
        expires_at: When an unanswered request expires.
        status: Where it stands.
        decided_by: The device that answered; None until a device did.
    """

    id: str
    task_id: TaskId
    title: str
    cell_id: CellId
    cell_name: str
    warden_id: WardenId
    role: WorkerRole
    scopes: tuple[str, ...]
    requested_at: datetime
    expires_at: datetime
    status: WatchStatus = WatchStatus.PENDING
    decided_by: DeviceId | None = None

    def settled(self, status: WatchStatus, device_id: DeviceId | None) -> WatchRequest:
        """Return this request settled as `status`, by `device_id` (None for an expiry)."""
        return replace(self, status=status, decided_by=device_id)


def new_watch_id(clock: Clock) -> str:
    """Mint a watch request id: `watch_` and a ULID timestamped by `clock`.

    Args:
        clock: Stamps the ULID, so ids sort by when the request was filed.

    Returns:
        A fresh id with 80 random bits, so one cannot be guessed from another.
    """
    timestamp_ms = int(clock.now().timestamp() * _MILLISECONDS_PER_SECOND)
    return f"{WATCH_ID_PREFIX}{encode_ulid(timestamp_ms, secrets.token_bytes(_ULID_RANDOM_BYTES))}"
