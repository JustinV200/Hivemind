"""Persist the device's leases: LeaseTable, written before anything that depends on it happens.

A lease (the Hive's temporary hold on a Real Cell, an existing device the Hive borrows for a
task) has to survive a crash of the Pollen Packet (the thin gateway program on the device): its
scratch directory, the processes it started and the paths it changed are written to this table
before ``cell.lease_opened`` is sent, before a started process's first output frame and before a
path outside scratch is changed, so a restart finds exactly what the packet had promised to undo
(gateway-contract sections 9.1 and 12). The table is one JSON document replaced atomically on
every change; it is small (a lease is a few hundred bytes plus one record per process and per
changed path). ``LeaseTable`` is the one owner of the in-memory copy: a change is applied to a
copy, written to disk, and only then becomes what readers see.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Opened by the composition
    root; read by the executors and the heartbeat; changed by pollen.agent.leases and the
    executors; calls into pollen.state (the atomic writer) and pollen.lease.lease only.

Key invariants:
    - What readers see is what the disk holds: a change that failed to write is not visible.
    - A missing or empty table file means no leases; an unreadable one is a StateError, never
      silently an empty table (that would orphan scratch directories and processes).
    - Session traffic is accepted only for a lease in OPEN state whose session is not closed.

See Also:
    - .claude/codingrules.md Appendix C (Device lease) for the machine and where it persists.
    - pollen.lease.lease for the DeviceLease value this table stores.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pollen.errors import LeaseNotOpenError, StateError
from pollen.lease.lease import DeviceLease, LeaseState
from pollen.state import write_atomic
from waggle.ids import LeaseId

__all__ = ["LeaseTable"]


class _LeaseDocument(BaseModel):
    """The table file's shape: a list of leases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    leases: tuple[DeviceLease, ...] = Field(default=(), description="Every lease the packet holds.")


class LeaseTable:
    """The persisted set of device leases, keyed by lease id.

    Owns mutable state (codingrules 8.5, documented): ``_leases`` is replaced entry by entry,
    only after the new table reached the disk. An ``asyncio.Lock`` serialises changes so two
    tasks cannot write each other's stale copy.
    """

    def __init__(self, path: Path, leases: Iterable[DeviceLease] = ()) -> None:
        """Wrap an already persisted set of leases.

        Args:
            path: The table file changes are written to.
            leases: What the file holds now.
        """
        self._path = path
        self._leases = {lease.lease_id: lease for lease in leases}
        self._lock = asyncio.Lock()

    @classmethod
    def load(cls, path: Path) -> LeaseTable:
        """Open the table on its file; a missing file is an empty table.

        Args:
            path: The table file.

        Returns:
            A table holding what the file says.

        Raises:
            StateError: The file exists but cannot be read or parsed.
        """
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return cls(path)
        except OSError as exc:
            raise StateError("The lease table file cannot be read.") from exc
        try:
            return cls(path, _LeaseDocument.model_validate_json(raw).leases)
        except ValidationError as exc:
            raise StateError("The lease table file is not a valid lease table.") from exc

    def get(self, lease_id: LeaseId) -> DeviceLease | None:
        """Return the lease with ``lease_id``, or None.

        Args:
            lease_id: The lease to look up.
        """
        return self._leases.get(lease_id)

    def require_session(self, lease_id: LeaseId) -> DeviceLease:
        """Return an OPEN lease whose session accepts traffic.

        Args:
            lease_id: The lease session traffic names.

        Returns:
            The lease.

        Raises:
            LeaseNotOpenError: The lease is unknown, releasing, or its session was closed.
        """
        lease = self._leases.get(lease_id)
        if lease is None or lease.state is not LeaseState.OPEN or lease.is_session_closed:
            raise LeaseNotOpenError(f"Lease {lease_id} is not open on this device.")
        return lease

    def all(self) -> tuple[DeviceLease, ...]:
        """Return every lease, open or releasing, oldest first."""
        return tuple(self._leases.values())

    def open_ids(self) -> tuple[LeaseId, ...]:
        """Return the ids of the leases in OPEN state, for the heartbeat."""
        return tuple(i for i, lease in self._leases.items() if lease.state is LeaseState.OPEN)

    def process_count(self) -> int:
        """Return how many processes the leases still track, for the heartbeat."""
        return sum(len(lease.processes) for lease in self._leases.values())

    async def put(self, lease: DeviceLease) -> None:
        """Add or replace ``lease`` and persist the table.

        Args:
            lease: The lease to store.

        Raises:
            StateError: The table could not be written; the lease is then not stored.
        """
        async with self._lock:
            await self._write({**self._leases, lease.lease_id: lease})

    async def modify(
        self, lease_id: LeaseId, change: Callable[[DeviceLease], DeviceLease]
    ) -> DeviceLease:
        """Apply ``change`` to the stored lease and persist the result.

        Args:
            lease_id: The lease to change.
            change: A pure function from the stored lease to its replacement.

        Returns:
            The replacement, now stored.

        Raises:
            LeaseNotOpenError: There is no such lease.
            StateError: The table could not be written; the lease is then unchanged.
        """
        async with self._lock:
            current = self._leases.get(lease_id)
            if current is None:
                raise LeaseNotOpenError(f"Lease {lease_id} is not held on this device.")
            updated = change(current)
            await self._write({**self._leases, lease_id: updated})
            return updated

    async def remove(self, lease_id: LeaseId) -> None:
        """Drop a released lease and persist the table; an unknown id is a no-op.

        Args:
            lease_id: The lease that has left the table.

        Raises:
            StateError: The table could not be written; the lease is then still stored.
        """
        async with self._lock:
            if lease_id in self._leases:
                await self._write({k: v for k, v in self._leases.items() if k != lease_id})

    async def _write(self, leases: dict[LeaseId, DeviceLease]) -> None:
        """Write ``leases`` to disk, then make them the table (called with the lock held)."""
        document = _LeaseDocument(leases=tuple(leases.values()))
        try:
            # Disk first, memory second: a failed write leaves the old table authoritative.
            await asyncio.to_thread(write_atomic, self._path, document.model_dump_json().encode())
        except OSError as exc:
            raise StateError("The lease table could not be written.") from exc
        self._leases = leases
