"""Define EntranceStore, the Entrance tables' protocol, and the guards every implementation applies.

The Entrance tables (the Hive Entrance's own SQLite tables; the Entrance is the Hive's one HTTP
door) hold the operator's password hash, the enrolled devices and their invites
(codingrules Appendix C: password hash and public keys only). ``EntranceStore`` is their seam
(codingrules 8.1), implemented by ``hivemind.entrance.store.sqlite.SqliteEntranceStore`` (the
Hive's own ``[hive] db`` file) and ``hivemind.entrance.store.memory.MemoryEntranceStore``. The
pure functions here are the rules both implementations apply inside their own atomic step, so
the two can never disagree: ``check_new_device`` lets a device enter only where the state
machine starts (INVITED, or APPROVED for the loopback-bound console), ``transition_device`` is
the only way a status changes (it calls ``assert_transition`` and checks the caller's expected
status), ``use_invite`` makes an invite single-use and refuses it once expired, and
``check_device_event`` makes sure the Pheromone Trail (audit log) event written with a change is
the one that change records. A status change's other field updates travel as ``DeviceChanges``,
keyword arguments typed per field; the id, the creation time, the console flag and the login
bookkeeping cannot be among them. The login bookkeeping has its own write, ``record_login``
(``apply_login`` is its rule): when an APPROVED device last logged in, from which network, and
its passkey's signature counter, which may only move forward. ``EntranceStore`` also extends
``AuthTables``: the session, login, pending-confirmation and mode tables (roadmap 10.5e).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Used by
    ``hivemind.entrance.enrol`` (the console bootstrap and every enrolment flow) and, later, the
    login and session routes. Calls into ``hivemind.entrance.enrol`` (models, state),
    ``hivemind.entrance.errors``, ``hivemind.common.errors`` and ``hivemind.pheromone``.

Key invariants:
    - No caller can skip the state machine: ``put_device`` accepts only an entry status, and
      ``update_device_status`` is the only status change, with the expected current status as an
      argument (so of two racing decisions, the second fails instead of overwriting the first).
    - An invite admits one INVITED device, once, before it expires; ``redeem_invite`` spends it
      and moves its device to PENDING in one atomic step, so neither happens without the other.
    - Every entry and every transition carries its ``guard.entrance_*`` trail event, written in
      the same atomic step as the state change (codingrules Appendix C): both commit, or neither.
    - ``record_login`` never changes a status, touches only an APPROVED device, and never moves a
      passkey's counter backwards (two logins racing with the same counter: the second fails).

See Also:
    - hivemind.entrance.enrol.state for the transition table and the kinds these guards apply.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, TypedDict, Unpack

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.auth.keys import KeyKind
from hivemind.entrance.enrol.models import (
    DeviceDescription,
    DeviceInvite,
    EnrolledDevice,
    OperatorCredential,
)
from hivemind.entrance.enrol.state import (
    APPROVED_TRAIL_KIND,
    ENTRY_TRAIL_KINDS,
    DeviceStatus,
    assert_transition,
    trail_kind,
)
from hivemind.entrance.errors import (
    DeviceStatusConflictError,
    InvalidDeviceEntryError,
    InviteAlreadyUsedError,
    InviteExpiredError,
    PasskeyRejectedError,
)
from hivemind.entrance.store.tables import AuthTables
from hivemind.pheromone import GuardEvent
from waggle.ids import DeviceId

__all__ = [
    "DeviceChanges",
    "EntranceStore",
    "GrantChanges",
    "apply_login",
    "check_device_event",
    "check_grant_change",
    "check_new_device",
    "check_status_change",
    "regrant_device",
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


class GrantChanges(TypedDict, total=False):
    """What re-granting an APPROVED device may change: what it may do and spend, nothing else."""

    capabilities: tuple[str, ...]
    spend_cap_usd_per_day: float | None


# Read once from the TypedDicts themselves, so the runtime guards and the static types never drift.
_CHANGEABLE_FIELDS = frozenset(DeviceChanges.__annotations__)
_GRANT_FIELDS = frozenset(GrantChanges.__annotations__)


class EntranceStore(AuthTables, Protocol):
    """Persist the operator row, enrolled devices and invites; apply the enrolment rules.

    Implementations serialise their own operations (one lock or one connection thread) and apply
    ``check_new_device``, ``check_status_change``, ``transition_device``, ``use_invite`` and
    ``apply_login`` inside the same atomic step as the write they guard, writing the change's
    trail event in that step. The four tables of ``AuthTables`` come with the store.
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

    async def put_device(self, device: EnrolledDevice, event: GuardEvent) -> None:
        """Record a new device at the state machine's entry, and its entry event, atomically.

        Args:
            device: The new record, INVITED (or APPROVED and loopback-bound, the console).
            event: Its ``ENTRY_TRAIL_KINDS`` event, about ``device.id``.

        Raises:
            DeviceAlreadyExistsError: A device with this id exists.
            InvalidDeviceEntryError: The status is not an entry status for this device.
            InvariantViolationError: ``event`` is not this entry's event.
            DuplicateEventError: ``event``'s id is already on the trail.
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
        event: GuardEvent,
        **changes: Unpack[DeviceChanges],
    ) -> EnrolledDevice:
        """Move a device along one edge, applying ``changes`` and recording ``event`` atomically.

        Args:
            device_id: The device to move.
            expected: The status the caller decided from; the stored one must still be it.
            new: The status to move to; ``expected`` to ``new`` must be an edge.
            event: The edge's ``guard.entrance_*`` event (``trail_kind``), about ``device_id``.
            **changes: Fields to set in the same step (``DeviceChanges``).

        Returns:
            The record as stored after the change.

        Raises:
            DeviceNotFoundError: No such device.
            InvalidDeviceTransitionError: ``expected`` to ``new`` is not an edge.
            InvariantViolationError: ``event`` is not this edge's event about this device.
            DeviceStatusConflictError: The stored status is not ``expected``.
            DuplicateEventError: ``event``'s id is already on the trail.
            pydantic.ValidationError: The changed record breaks a model rule (an approval
                without ``approved_at``, a redemption without a key).
        """
        ...

    async def update_device_grant(
        self, device_id: DeviceId, event: GuardEvent, **changes: Unpack[GrantChanges]
    ) -> EnrolledDevice:
        """Re-grant an APPROVED device's capabilities and cap, recording ``event`` atomically.

        Args:
            device_id: The device, APPROVED.
            event: Its ``guard.entrance_approved`` event (a re-grant is an approval of the new
                set), about ``device_id``.
            **changes: The new set and cap (``GrantChanges``).

        Returns:
            The record as stored after the change.

        Raises:
            DeviceNotFoundError: No such device.
            DeviceStatusConflictError: The device is not APPROVED.
            InvariantViolationError: ``event`` is not an approval about this device.
            DuplicateEventError: ``event``'s id is already on the trail.
            pydantic.ValidationError: The changed record breaks a model rule.
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

    async def redeem_invite(
        self,
        code_hash: str,
        used_at: datetime,
        event: GuardEvent,
        **changes: Unpack[DeviceChanges],
    ) -> EnrolledDevice:
        """Spend the invite and move its device INVITED to PENDING with ``changes``, atomically.

        Args:
            code_hash: SHA-256 of the presented code.
            used_at: When it is being redeemed.
            event: The ``guard.entrance_pending`` event about the invite's device.
            **changes: The redemption's fields: the key, the description, the request's expiry.

        Returns:
            The device as stored, PENDING.

        Raises:
            InviteNotFoundError: No such invite.
            InviteAlreadyUsedError: It was used before.
            InviteExpiredError: ``used_at`` is at or past its expiry.
            InvariantViolationError: ``event`` is not the pending event about its device.
            DeviceStatusConflictError: Its device is no longer INVITED (cancelled, expired).
            DuplicateEventError: ``event``'s id is already on the trail.
            pydantic.ValidationError: The PENDING record breaks a model rule.
        """
        ...

    async def record_login(
        self, device_id: DeviceId, at: datetime, network: str | None, sign_count: int | None
    ) -> EnrolledDevice:
        """Record a successful login on an APPROVED device (``apply_login``), atomically.

        Args:
            device_id: The device that logged in.
            at: When; becomes ``last_seen_at``.
            network: The network it logged in from; becomes ``last_network`` (None: unknown).
            sign_count: A passkey's new signature counter; None for an Ed25519 device.

        Returns:
            The device as stored after the change.

        Raises:
            DeviceNotFoundError: No such device.
            DeviceStatusConflictError: It is not (or no longer) APPROVED.
            PasskeyRejectedError: ``sign_count`` does not move the stored counter forward.
        """
        ...


def check_new_device(device: EnrolledDevice, event: GuardEvent) -> None:
    """Refuse a new record anywhere but the state machine's entry, or without its entry event.

    Args:
        device: The record ``put_device`` was given.
        event: The event it was given with.

    Raises:
        InvalidDeviceEntryError: Not INVITED (and not loopback-bound), and not the loopback-bound
            console entering APPROVED.
        InvariantViolationError: ``event`` is not the entry's ``ENTRY_TRAIL_KINDS`` event about
            this device.
    """
    # The two entries ADR-0033 allows: a remote device through an invite, and the console,
    # recorded approved by the operator bootstrap on the Hive Stand itself.
    is_invited = device.status is DeviceStatus.INVITED and not device.loopback_bound
    is_console = device.status is DeviceStatus.APPROVED and device.loopback_bound
    if not (is_invited or is_console):
        raise InvalidDeviceEntryError(device.id, device.status)
    check_device_event(device.id, ENTRY_TRAIL_KINDS[device.status], event)


def check_status_change(
    device_id: DeviceId, expected: DeviceStatus, new: DeviceStatus, event: GuardEvent
) -> None:
    """Refuse a status change that is no edge, or whose event is not that edge's.

    Args:
        device_id: The device being moved.
        expected: The status the caller decided from.
        new: The status it asked for.
        event: The event it gave.

    Raises:
        InvalidDeviceTransitionError: ``expected`` to ``new`` is not an edge.
        InvariantViolationError: ``event`` is not the edge's event about ``device_id``.
    """
    # The edge first: asking for an impossible move is a bug whatever event came with it.
    assert_transition(expected, new, device_id)
    check_device_event(device_id, trail_kind(expected, new), event)


def check_device_event(device_id: DeviceId, kind: str, event: GuardEvent) -> None:
    """Require ``event`` to be the ``kind`` event about ``device_id``, so the trail never lies.

    Args:
        device_id: The device the state change is about.
        kind: The ``guard.entrance_*`` kind that change is recorded as.
        event: The event about to be written with it.

    Raises:
        InvariantViolationError: ``event`` is about another subject or of another kind.
    """
    if event.subject_id != device_id or event.kind != kind:
        raise InvariantViolationError(
            f"Event {event.id} is {event.kind} about {event.subject_id}; a change of device "
            f"{device_id} must record {kind} about that device."
        )


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


def check_grant_change(device_id: DeviceId, event: GuardEvent) -> None:
    """Require a re-grant's event to be an approval about the device.

    Args:
        device_id: The device being re-granted.
        event: The event about to be written with it.

    Raises:
        InvariantViolationError: ``event`` is of another kind or about another device.
    """
    check_device_event(device_id, APPROVED_TRAIL_KIND, event)


def regrant_device(current: EnrolledDevice, changes: Mapping[str, object]) -> EnrolledDevice:
    """Apply one re-grant to ``current``: the single place an approved device's set changes.

    Args:
        current: The record as stored right now.
        changes: The ``GrantChanges`` given.

    Returns:
        A new, fully re-validated record.

    Raises:
        DeviceStatusConflictError: ``current`` is not APPROVED.
        TypeError: ``changes`` names a field a re-grant may not set.
        pydantic.ValidationError: The result breaks a model rule.
    """
    # Only an approved device's grant changes; a locked one is unlocked first, on loopback.
    if current.status is not DeviceStatus.APPROVED:
        raise DeviceStatusConflictError(current.id, DeviceStatus.APPROVED, current.status)
    forbidden = set(changes) - _GRANT_FIELDS
    if forbidden:
        raise TypeError(f"A re-grant cannot set {sorted(forbidden)}.")
    # model_validate (not model_copy) so every field and cross-field rule runs on the result.
    fields: dict[str, object] = dict(current)
    fields.update(changes)
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


def apply_login(
    current: EnrolledDevice, at: datetime, network: str | None, sign_count: int | None
) -> EnrolledDevice:
    """Apply one successful login to ``current``: the rule ``record_login`` applies atomically.

    Args:
        current: The device as stored right now.
        at: When it logged in.
        network: The network it logged in from, or None.
        sign_count: A passkey's new counter, or None for an Ed25519 device.

    Returns:
        A new, fully re-validated record with the login applied; its status is unchanged.

    Raises:
        DeviceStatusConflictError: ``current`` is not APPROVED.
        PasskeyRejectedError: ``sign_count`` does not move the counter forward (both 0, which
            many platform authenticators always report, is allowed).
        pydantic.ValidationError: The result breaks a model rule (a malformed network).
    """
    if current.status is not DeviceStatus.APPROVED:
        raise DeviceStatusConflictError(current.id, DeviceStatus.APPROVED, current.status)
    fields: dict[str, object] = {**dict(current), "last_seen_at": at, "last_network": network}
    if sign_count is not None:
        # Re-checked here, against the row as it is now: two assertions verified against the same
        # stored counter can both pass verification, but only the first may move it.
        stalled = sign_count <= current.sign_count and not sign_count == current.sign_count == 0
        if stalled:
            raise PasskeyRejectedError(
                f"The passkey of device {current.id} reported a signature counter that did not "
                "move forward."
            )
        fields["sign_count"] = sign_count
    # model_validate (not model_copy) so every field and cross-field rule runs on the result.
    return EnrolledDevice.model_validate(fields)
