"""Provide MemoryEntranceStore: the Entrance tables in dicts, for tests and demos.

Codingrules 14.4 keeps fakes beside their protocol, honest and production quality. This one holds
the Hive Entrance's (the Hive's one HTTP door) operator row, devices and invites in memory, guarded
by one lock, and applies the same guards ``hivemind.entrance.store.sqlite.SqliteEntranceStore``
applies (``hivemind.entrance.store.protocol``: entry, transition, single use, the event check),
plus the same referential rules the SQLite schema enforces (an invite names an existing, INVITED
device, and at most one invite per device), so the contract suite runs unchanged over both. Every
change's ``guard.entrance_*`` event is recorded on the Pheromone Trail (the Hive's audit log) it
was built over, before the dicts change, so a failed record leaves them as they were: the memory
form of "the event commits with the state change, or neither does".

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Used by tests,
    demos and any composition root that wants the Entrance tables without a database file. Calls
    into ``hivemind.entrance.enrol``, ``hivemind.entrance.errors``, the protocol's guards and a
    ``hivemind.pheromone.PheromoneTrail``.

Key invariants:
    - Behaves exactly like SqliteEntranceStore under
      ``tests/contracts/test_entrance_store_contract.py``.
    - Every method holds the lock for its whole body, so a read-decide-write step (a status
      change, an invite use) is atomic against every other call.
    - Every mutation validates first, then records its event, and only then swaps in the new
      values: a refused change and a failed record both leave the dicts exactly as they were.

See Also:
    - hivemind.entrance.store.protocol for EntranceStore and the guards applied here.
    - hivemind.brood_chamber.store.memory for the same "validate, record, then swap in" shape.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Unpack

from hivemind.entrance.enrol.models import DeviceInvite, EnrolledDevice, OperatorCredential
from hivemind.entrance.enrol.state import DeviceStatus
from hivemind.entrance.errors import (
    DeviceAlreadyExistsError,
    DeviceNotFoundError,
    DeviceStatusConflictError,
    InviteAlreadyExistsError,
    InviteNotFoundError,
)
from hivemind.entrance.store.protocol import (
    DeviceChanges,
    check_new_device,
    check_status_change,
    transition_device,
    use_invite,
)
from hivemind.pheromone import GuardEvent, PheromoneTrail
from waggle.ids import DeviceId

__all__ = ["MemoryEntranceStore"]


class MemoryEntranceStore:
    """The Entrance tables in three in-process structures, gone when the process exits."""

    def __init__(self, trail: PheromoneTrail) -> None:
        """Create empty tables that record their events on ``trail``.

        Args:
            trail: Where every entry's and every status change's event is recorded, before the
                change is applied; normally the trail the rest of the Entrance records on.
        """
        self._trail = trail
        self._operator: OperatorCredential | None = None
        self._devices: dict[DeviceId, EnrolledDevice] = {}
        self._invites: dict[str, DeviceInvite] = {}
        # Serialises every method, so each read-decide-write step is atomic (module docstring).
        self._lock = asyncio.Lock()

    async def get_operator(self) -> OperatorCredential | None:
        """Return the operator row; see EntranceStore.get_operator."""
        async with self._lock:
            return self._operator

    async def set_operator_password_hash(self, password_hash: str, at: datetime) -> None:
        """Create or change the operator row; see EntranceStore.set_operator_password_hash."""
        async with self._lock:
            created_at = self._operator.created_at if self._operator is not None else at
            self._operator = OperatorCredential(
                password_hash=password_hash, created_at=created_at, changed_at=at
            )

    async def put_device(self, device: EnrolledDevice, event: GuardEvent) -> None:
        """Record a new device and its entry event; see EntranceStore.put_device."""
        check_new_device(device, event)
        async with self._lock:
            if device.id in self._devices:
                raise DeviceAlreadyExistsError(device.id)
            # Latency: an in-memory append (or the durable trail's local write); recorded first
            # so a failed record leaves no device behind.
            await self._trail.record(event)
            self._devices[device.id] = device

    async def get_device(self, device_id: DeviceId) -> EnrolledDevice:
        """Return one device; see EntranceStore.get_device."""
        async with self._lock:
            return self._require_device(device_id)

    async def list_devices(self, status: DeviceStatus | None = None) -> tuple[EnrolledDevice, ...]:
        """Return devices, oldest first; see EntranceStore.list_devices."""
        async with self._lock:
            devices = list(self._devices.values())
        # Filtered and sorted outside the lock: the snapshot above is already a private copy.
        matches = [device for device in devices if status is None or device.status is status]
        matches.sort(key=lambda device: (device.created_at, device.id))
        return tuple(matches)

    async def update_device_status(
        self,
        device_id: DeviceId,
        expected: DeviceStatus,
        new: DeviceStatus,
        event: GuardEvent,
        **changes: Unpack[DeviceChanges],
    ) -> EnrolledDevice:
        """Move a device along the state machine; see EntranceStore.update_device_status."""
        check_status_change(device_id, expected, new, event)
        async with self._lock:
            updated = transition_device(self._require_device(device_id), expected, new, changes)
            # Validated above, recorded here, applied last: see the module's key invariants.
            await self._trail.record(event)
            self._devices[device_id] = updated
            return updated

    async def put_invite(self, invite: DeviceInvite) -> None:
        """Record an invite for an INVITED device; see EntranceStore.put_invite."""
        async with self._lock:
            device = self._require_device(invite.device_id)
            # Mirrors the SQLite schema's primary key and UNIQUE(device_id): one code, one device.
            if invite.code_hash in self._invites or any(
                existing.device_id == invite.device_id for existing in self._invites.values()
            ):
                raise InviteAlreadyExistsError(
                    f"An invite with code hash {invite.code_hash[:12]}... or for device "
                    f"{invite.device_id} already exists."
                )
            if device.status is not DeviceStatus.INVITED:
                raise DeviceStatusConflictError(device.id, DeviceStatus.INVITED, device.status)
            self._invites[invite.code_hash] = invite

    async def get_invite(self, code_hash: str) -> DeviceInvite:
        """Return one invite; see EntranceStore.get_invite."""
        async with self._lock:
            return self._require_invite(code_hash)

    async def redeem_invite(
        self,
        code_hash: str,
        used_at: datetime,
        event: GuardEvent,
        **changes: Unpack[DeviceChanges],
    ) -> EnrolledDevice:
        """Spend an invite and move its device to PENDING; see EntranceStore.redeem_invite."""
        async with self._lock:
            # The invite's own rules first (single use, expiry), then its device's edge.
            used = use_invite(self._require_invite(code_hash), used_at)
            check_status_change(used.device_id, DeviceStatus.INVITED, DeviceStatus.PENDING, event)
            current = self._require_device(used.device_id)
            updated = transition_device(
                current, DeviceStatus.INVITED, DeviceStatus.PENDING, changes
            )
            # Validated above, recorded here, both applied last: see the module's key invariants.
            await self._trail.record(event)
            self._invites[code_hash] = used
            self._devices[updated.id] = updated
            return updated

    def _require_device(self, device_id: DeviceId) -> EnrolledDevice:
        """Return a device or raise DeviceNotFoundError; the caller holds the lock."""
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        return device

    def _require_invite(self, code_hash: str) -> DeviceInvite:
        """Return an invite or raise InviteNotFoundError; the caller holds the lock."""
        invite = self._invites.get(code_hash)
        if invite is None:
            raise InviteNotFoundError(code_hash)
        return invite
