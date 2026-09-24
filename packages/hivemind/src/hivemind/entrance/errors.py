"""Define EntranceError and every way the Hive Entrance refuses something on purpose.

The Hive Entrance (the only door into the Hive, codingrules 8.15) refuses a lot, by design: a weak
password, a passkey ceremony that does not verify, a wrapped key the password does not open, a
device status change its state machine forbids, an invite used twice. Every such refusal is one
class here, rooted at ``EntranceError`` so a caller can catch the whole family, and each also
subclasses the ``hivemind.common.errors`` category it belongs to (``NotFoundError``,
``ConflictError``, ``PermissionDeniedError``) so a generic handler (an HTTP status mapper, the CLI)
can react by category without knowing the Entrance. Every class carries its own stable, dotted
``code`` (codingrules section 10), the form an error takes once it crosses the Landing Board (the
Entrance's versioned API).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Raised by
    ``hivemind.entrance.auth`` (passwords, keys, passkeys, wrapped keys),
    ``hivemind.entrance.enrol`` (the device state machine, the console bootstrap) and
    ``hivemind.entrance.store`` (the Entrance tables). Imports only ``hivemind.common.errors``.

Key invariants:
    - Every class sets its own ``code``; no two share one (a test walks them all).
    - No message ever carries a password, a key, an invite code or a session token: messages name
      devices, statuses and invites by id or hash only (codingrules sections 12 and 13).
    - ``PasskeyRejectedError`` and ``KeyUnwrapError`` never say which check failed beyond what the
      verifying library reports about the ceremony itself: a wrong password and a tampered blob
      read the same (ADR-0033: a failure never says which factor failed).
    - Statuses are typed as ``Enum`` here, not ``DeviceStatus``, so this module never imports
      ``hivemind.entrance.enrol.state``, which imports it.

See Also:
    - .claude/codingrules.md section 10 for the error rules this module follows.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for what is refused.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import (
    ConflictError,
    HiveMindError,
    NotFoundError,
    PermissionDeniedError,
)

__all__ = [
    "DeviceAlreadyExistsError",
    "DeviceNotFoundError",
    "DeviceStatusConflictError",
    "EntranceError",
    "InvalidDeviceEntryError",
    "InvalidDeviceTransitionError",
    "InviteAlreadyExistsError",
    "InviteAlreadyUsedError",
    "InviteExpiredError",
    "InviteNotFoundError",
    "KeyUnwrapError",
    "OperatorAlreadyInitialisedError",
    "OperatorNotInitialisedError",
    "OperatorPasswordMismatchError",
    "PasskeyRejectedError",
    "WeakPasswordError",
]


class EntranceError(HiveMindError):
    """Root of every error the Hive Entrance raises on purpose."""

    code: ClassVar[str] = "hivemind.entrance.error"


# ──────────────────────────────────────────────────────────────────────────────
# Credentials: passwords, passkeys, wrapped keys
# ──────────────────────────────────────────────────────────────────────────────


class WeakPasswordError(EntranceError):
    """Raise when a new operator password is too short or too long to hash.

    The message states the rule, never the password or its length.
    """

    code: ClassVar[str] = "hivemind.entrance.weak_password"


class PasskeyRejectedError(EntranceError, PermissionDeniedError):
    """Raise when a WebAuthn registration or assertion does not verify for this relying party."""

    code: ClassVar[str] = "hivemind.entrance.passkey_rejected"


class KeyUnwrapError(EntranceError, PermissionDeniedError):
    """Raise when a password-wrapped key cannot be opened, whatever the reason.

    A wrong password, a tampered blob and a malformed one all raise this with the same message, so
    the error is never an oracle for which one it was.
    """

    code: ClassVar[str] = "hivemind.entrance.key_unwrap_failed"

    def __init__(self, name: str) -> None:
        """Build the error for the wrapped secret ``name``.

        Args:
            name: The secret's name in the secret store (never its value).
        """
        super().__init__(f"The wrapped key {name!r} could not be opened with this password.")
        self.name = name


# ──────────────────────────────────────────────────────────────────────────────
# The operator
# ──────────────────────────────────────────────────────────────────────────────


class OperatorAlreadyInitialisedError(EntranceError, ConflictError):
    """Raise when the operator is bootstrapped a second time; changing the password is separate."""

    code: ClassVar[str] = "hivemind.entrance.operator_already_initialised"


class OperatorNotInitialisedError(EntranceError, NotFoundError):
    """Raise when an operation needs the operator (or the console's key) and neither exists yet."""

    code: ClassVar[str] = "hivemind.entrance.operator_not_initialised"


class OperatorPasswordMismatchError(EntranceError, PermissionDeniedError):
    """Raise when the current operator password presented to change it is not the right one."""

    code: ClassVar[str] = "hivemind.entrance.operator_password_mismatch"


# ──────────────────────────────────────────────────────────────────────────────
# Enrolled devices and their state machine
# ──────────────────────────────────────────────────────────────────────────────


class DeviceNotFoundError(EntranceError, NotFoundError):
    """Raise when no enrolled-device record has the requested id."""

    code: ClassVar[str] = "hivemind.entrance.device_not_found"

    def __init__(self, device_id: str) -> None:
        """Build the error for a missing device.

        Args:
            device_id: The id that was looked up.
        """
        super().__init__(f"No enrolled device {device_id} exists in the Entrance tables.")
        self.device_id = device_id


class DeviceAlreadyExistsError(EntranceError, ConflictError):
    """Raise when a device record is added under an id the Entrance tables already hold."""

    code: ClassVar[str] = "hivemind.entrance.device_exists"

    def __init__(self, device_id: str) -> None:
        """Build the error for a duplicate device id.

        Args:
            device_id: The id that is already taken.
        """
        super().__init__(f"Enrolled device {device_id} already exists; ids are never reused.")
        self.device_id = device_id


class InvalidDeviceTransitionError(EntranceError, ConflictError):
    """Raise when a device status change is not an edge of the enrolled-device state machine."""

    code: ClassVar[str] = "hivemind.entrance.invalid_device_transition"

    def __init__(self, from_status: Enum, to_status: Enum, device_id: str | None = None) -> None:
        """Build the error for a forbidden edge.

        Args:
            from_status: The status the device is in (or is expected to be in).
            to_status: The status a caller asked to move it to.
            device_id: The device's id, when the caller has it.
        """
        subject = f" device {device_id}" if device_id is not None else " a device"
        super().__init__(
            f"Cannot move{subject} from {from_status.name} to {to_status.name}: no such edge "
            "exists in the enrolled-device state machine."
        )
        self.from_status = from_status
        self.to_status = to_status
        self.device_id = device_id


class InvalidDeviceEntryError(EntranceError, ConflictError):
    """Raise when a new device record would enter the state machine anywhere but its entry.

    A device enters as INVITED (an invite minted on loopback); only the Hive Stand's own
    loopback-bound console is recorded straight as APPROVED (ADR-0033). Anything else would skip
    enrolment altogether.
    """

    code: ClassVar[str] = "hivemind.entrance.invalid_device_entry"

    def __init__(self, device_id: str, status: Enum) -> None:
        """Build the error for a record that tried to skip enrolment.

        Args:
            device_id: The new record's id.
            status: The status it tried to enter with.
        """
        super().__init__(
            f"Device {device_id} cannot be recorded as {status.name}: a device enters as "
            "INVITED, and only the loopback-bound Hive Stand console enters as APPROVED."
        )
        self.device_id = device_id
        self.status = status


class DeviceStatusConflictError(EntranceError, ConflictError):
    """Raise when a device is not in the status a caller expected when it asked to move it.

    The expected status is part of every status change so two concurrent decisions (an approval
    racing an expiry sweep) can never both win: the second one finds the status moved and fails.
    """

    code: ClassVar[str] = "hivemind.entrance.device_status_conflict"

    def __init__(self, device_id: str, expected: Enum, actual: Enum) -> None:
        """Build the error for a stale expectation.

        Args:
            device_id: The device whose status moved.
            expected: What the caller believed the status was.
            actual: What the Entrance tables hold.
        """
        super().__init__(
            f"Device {device_id} is {actual.name}, not {expected.name} as the caller expected; "
            "re-read it and decide again."
        )
        self.device_id = device_id
        self.expected = expected
        self.actual = actual


# ──────────────────────────────────────────────────────────────────────────────
# Invites
# ──────────────────────────────────────────────────────────────────────────────


class InviteNotFoundError(EntranceError, NotFoundError):
    """Raise when no invite has the given code hash."""

    code: ClassVar[str] = "hivemind.entrance.invite_not_found"

    def __init__(self, code_hash: str) -> None:
        """Build the error for an unknown invite.

        Args:
            code_hash: The SHA-256 of the presented code (never the code itself).
        """
        super().__init__(f"No invite has code hash {code_hash[:12]}...")
        self.code_hash = code_hash


class InviteAlreadyExistsError(EntranceError, ConflictError):
    """Raise when an invite reuses a code hash, or names a device that already has an invite."""

    code: ClassVar[str] = "hivemind.entrance.invite_exists"


class InviteAlreadyUsedError(EntranceError, ConflictError):
    """Raise when a single-use invite is presented a second time."""

    code: ClassVar[str] = "hivemind.entrance.invite_used"

    def __init__(self, code_hash: str) -> None:
        """Build the error for a spent invite.

        Args:
            code_hash: The SHA-256 of the presented code (never the code itself).
        """
        super().__init__(f"Invite {code_hash[:12]}... was already used; an invite admits once.")
        self.code_hash = code_hash


class InviteExpiredError(EntranceError, ConflictError):
    """Raise when an invite is presented after its expiry."""

    code: ClassVar[str] = "hivemind.entrance.invite_expired"

    def __init__(self, code_hash: str) -> None:
        """Build the error for a lapsed invite.

        Args:
            code_hash: The SHA-256 of the presented code (never the code itself).
        """
        super().__init__(f"Invite {code_hash[:12]}... has expired; mint a new one on loopback.")
        self.code_hash = code_hash
