"""Define EntranceStore, the Entrance tables' protocol, and the guards every implementation applies.

The Entrance tables (the Hive Entrance's own SQLite tables; the Entrance is the Hive's one HTTP
door) hold the operator's password hash, the enrolled devices and their invites
(codingrules Appendix C: password hash and public keys only). ``EntranceStore`` is their seam
(codingrules 8.1), implemented by ``hivemind.entrance.store.sqlite.SqliteEntranceStore`` (the
Hive's own ``[hive] db`` file) and ``hivemind.entrance.store.memory.MemoryEntranceStore``. Three
pure functions here are the rules both implementations apply inside their own atomic step, so
the two can never disagree: ``check_new_device`` lets a device enter only where the state
machine starts (INVITED, or APPROVED for the loopback-bound console), ``transition_device`` is
the only way a status changes (it calls ``assert_transition`` and checks the caller's expected
status), and ``use_invite`` makes an invite single-use and refuses it once expired. A status
change's other field updates travel as ``DeviceChanges``, keyword arguments typed per field;
the id, the creation time, the console flag and the login bookkeeping cannot be among them.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Used by
    ``hivemind.entrance.enrol.console`` and, later, the enrolment, login and revocation routes.
    Calls into ``hivemind.entrance.enrol`` (models, state) and ``hivemind.entrance.errors``.

Key invariants:
    - No caller can skip the state machine: ``put_device`` accepts only an entry status, and
      ``update_device_status`` is the only status change, with the expected current status as an
      argument (so of two racing decisions, the second fails instead of overwriting the first).
    - An invite admits one INVITED device, once, before it expires.
    - Every transition is a ``guard.entrance_*`` trail event (Appendix C rule 3), declared by
      ``hivemind.pheromone.GuardEvent``; this data half of roadmap 10.5d writes the state change
      only, and the behaviour half adds the event write in the same transaction.

See Also:
    - hivemind.entrance.enrol.state for the transition table these guards apply.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, TypedDict, Unpack

from hivemind.entrance.auth.keys import KeyKind
from hivemind.entrance.enrol.models import (
    DeviceDescription,
    DeviceInvite,
    EnrolledDevice,
    OperatorCredential,
)
from hivemind.entrance.enrol.state import DeviceStatus, assert_transition
from hivemind.entrance.errors import (
    DeviceStatusConflictError,
    InvalidDeviceEntryError,
    InviteAlreadyUsedError,
    InviteExpiredError,
)
from waggle.ids import DeviceId

__all__ = [
    "DeviceChanges",
    "EntranceStore",
    "check_new_device",
    "transition_device",
    "use_invite",
]


class DeviceChanges(TypedDict, total=False):
    """The fields a status change may set alongside the new status; every other field is kept.

    Redemption (INVITED to PENDING) sets the key fields, the description and the request's
    expiry; approval sets the name, capabilities, spend cap, expiry, interactivity and
    ``approved_at``. ``id``, ``status``, ``created_at``, ``loopback_bound``, ``last_seen_at`` and
    ``last_network`` are deliberately absent: they are fixed at creation or kept by login.
    """

    name: str
    key_kind: KeyKind
    public_key: str
    credential_id: str
    sign_count: int
    rp_id: str
    backup_eligible: bool
    backup_state: bool
    interactive: bool
    description: DeviceDescription
    capabilities: tuple[str, ...]
    spend_cap_usd_per_day: float | None
    expires_at: datetime | None
    approved_at: datetime


# Read once from the TypedDict itself, so the runtime guard and the static type never drift.
_CHANGEABLE_FIELDS = frozenset(DeviceChanges.__annotations__)


class EntranceStore(Protocol):
    """Persist the operator row, enrolled devices and invites; apply the enrolment rules.

    Implementations serialise their own operations (one lock or one connection thread) and apply
    ``check_new_device``, ``transition_device`` and ``use_invite`` inside the same atomic step as
    the write they guard.
    """

    async def get_operator(self) -> OperatorCredential | None:
        """Return the operator row, or None before the operator has been bootstrapped.

        Returns:
            The password hash and its timestamps, or None.
        """
        ...

    async def set_operator_password_hash(self, password_hash: str, at: datetime) -> None:
        """Set the operator's password hash: the first time creates the row, later ones change it.

        Args:
            password_hash: An Argon2id PHC string (``PasswordHasher.hash``), never a password.
            at: When; becomes ``created_at`` the first time and ``changed_at`` every time.

        Raises:
            pydantic.ValidationError: ``password_hash`` is not an Argon2id PHC string.
        """
        ...

    async def put_device(self, device: EnrolledDevice) -> None:
        """Record a new device at the state machine's entry.

        Args:
            device: The new record, INVITED (or APPROVED and loopback-bound, the console).

        Raises:
            DeviceAlreadyExistsError: A device with this id exists.
            InvalidDeviceEntryError: The status is not an entry status for this device.
        """
        ...

    async def get_device(self, device_id: DeviceId) -> EnrolledDevice:
        """Return one device.

        Args:
            device_id: The device to read.

        Returns:
            The stored record.

        Raises:
            DeviceNotFoundError: No such device.
        """
        ...

    async def list_devices(self, status: DeviceStatus | None = None) -> tuple[EnrolledDevice, ...]:
        """Return every device, or every device in ``status``, oldest first.

        Args:
            status: Only devices in this status; None for all of them.

        Returns:
            The devices ordered by ``created_at``, then id.
        """
        ...

    async def update_device_status(
        self,
        device_id: DeviceId,
        expected: DeviceStatus,
        new: DeviceStatus,
        **changes: Unpack[DeviceChanges],
    ) -> EnrolledDevice:
        """Move a device from ``expected`` to ``new`` along the state machine, applying ``changes``.

        Args:
            device_id: The device to move.
            expected: The status the caller decided from; the stored one must still be it.
            new: The status to move to; ``expected`` to ``new`` must be an edge.
            **changes: Fields to set in the same step (``DeviceChanges``).

        Returns:
            The record as stored after the change.

        Raises:
            DeviceNotFoundError: No such device.
            InvalidDeviceTransitionError: ``expected`` to ``new`` is not an edge.
            DeviceStatusConflictError: The stored status is not ``expected``.
            pydantic.ValidationError: The changed record breaks a model rule (an approval
                without ``approved_at``, a redemption without a key).
        """
        ...

    async def put_invite(self, invite: DeviceInvite) -> None:
        """Record an invite for an INVITED device.

        Args:
            invite: The invite, unused.

        Raises:
            DeviceNotFoundError: Its device does not exist.
            DeviceStatusConflictError: Its device is not INVITED.
            InviteAlreadyExistsError: Its code hash is taken, or its device already has one.
        """
        ...

    async def get_invite(self, code_hash: str) -> DeviceInvite:
        """Return the invite with this code hash.

        Args:
            code_hash: SHA-256 of the presented code, lowercase hex.

        Returns:
            The stored invite, used or not.

        Raises:
            InviteNotFoundError: No such invite.
        """
        ...

    async def mark_invite_used(self, code_hash: str, used_at: datetime) -> DeviceInvite:
        """Spend the invite: the first call before its expiry succeeds, every later one fails.

        Args:
            code_hash: SHA-256 of the presented code.
            used_at: When it is being redeemed.

        Returns:
            The invite as stored, ``used_at`` set.

        Raises:
            InviteNotFoundError: No such invite.
            InviteAlreadyUsedError: It was used before.
            InviteExpiredError: ``used_at`` is at or past its expiry.
        """
        ...


def check_new_device(device: EnrolledDevice) -> None:
    """Refuse a new record anywhere but the state machine's entry.

    Args:
        device: The record ``put_device`` was given.

    Raises:
        InvalidDeviceEntryError: Not INVITED (and not loopback-bound), and not the loopback-bound
            console entering APPROVED.
    """
    # The two entries ADR-0033 allows: a remote device through an invite, and the console,
    # recorded approved by the operator bootstrap on the Hive Stand itself.
    is_invited = device.status is DeviceStatus.INVITED and not device.loopback_bound
    is_console = device.status is DeviceStatus.APPROVED and device.loopback_bound
    if not (is_invited or is_console):
        raise InvalidDeviceEntryError(device.id, device.status)


def transition_device(
    current: EnrolledDevice,
    expected: DeviceStatus,
    new: DeviceStatus,
    changes: Mapping[str, object],
) -> EnrolledDevice:
    """Apply one status change to ``current``: the single place a device's status moves.

    Args:
        current: The record as stored right now.
        expected: The status the caller decided from.
        new: The status to move to.
        changes: The ``DeviceChanges`` given with it.

    Returns:
        A new, fully re-validated record with ``new`` and ``changes`` applied.

    Raises:
        InvalidDeviceTransitionError: ``expected`` to ``new`` is not an edge.
        DeviceStatusConflictError: ``current`` is not in ``expected``.
        TypeError: ``changes`` names a field a status change may not set.
        pydantic.ValidationError: The result breaks a model rule.
    """
    # The edge first: asking for an impossible move is a bug whatever the stored status is.
    assert_transition(expected, new, current.id)
    if current.status is not expected:
        raise DeviceStatusConflictError(current.id, expected, current.status)
    # The static type already limits the keys; this also stops a **dict built at runtime from
    # smuggling in an immutable field (the id, the console flag) the model would happily accept.
    forbidden = set(changes) - _CHANGEABLE_FIELDS
    if forbidden:
        raise TypeError(f"A status change cannot set {sorted(forbidden)}.")
    # model_validate (not model_copy) so every field and cross-field rule runs on the result.
    fields: dict[str, object] = dict(current)
    fields.update(changes)
    fields["status"] = new
    return EnrolledDevice.model_validate(fields)


def use_invite(invite: DeviceInvite, used_at: datetime) -> DeviceInvite:
    """Spend ``invite`` at ``used_at``: the single-use and expiry rule both stores apply.

    Args:
        invite: The invite as stored right now.
        used_at: When it is being redeemed.

    Returns:
        The invite with ``used_at`` set.

    Raises:
        InviteAlreadyUsedError: It already has a ``used_at``.
        InviteExpiredError: ``used_at`` is at or past ``expires_at``.
    """
    if invite.used_at is not None:
        raise InviteAlreadyUsedError(invite.code_hash)
    if used_at >= invite.expires_at:
        raise InviteExpiredError(invite.code_hash)
    # model_validate, not model_copy, so the model's own window rule still checks used_at.
    return DeviceInvite.model_validate({**dict(invite), "used_at": used_at})
