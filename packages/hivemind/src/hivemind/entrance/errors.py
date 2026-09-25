"""Define EntranceError and every way the Hive Entrance refuses something on purpose.

The Hive Entrance (the only door into the Hive, codingrules 8.15) refuses a lot, by design: a weak
password, a passkey ceremony that does not verify, a wrapped key the password does not open, a
device status change its state machine forbids, an invite used twice, an approval wider than the
device ceiling, a revocation of the Hive Stand's own console. Every such refusal is one
class here, rooted at ``EntranceError`` so a caller can catch the whole family, and each also
subclasses the ``hivemind.common.errors`` category it belongs to (``NotFoundError``,
``ConflictError``, ``PermissionDeniedError``) so a generic handler (an HTTP status mapper, the CLI)
can react by category without knowing the Entrance. Every class carries its own stable, dotted
``code`` (codingrules section 10), the form an error takes once it crosses the Landing Board (the
Entrance's versioned API). Roadmap step 10.5e adds the refusals of login and sessions (one generic
``AuthenticationFailedError``), step-up, pending confirmations, the travel lock and the Entrance
Reducer; the read routes add ``CellNotFoundError``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Raised by
    ``hivemind.entrance.auth`` (passwords, keys, passkeys, wrapped keys, challenges, login,
    sessions, step-up, pending confirmations, the travel lock), ``hivemind.entrance.enrol`` (the
    device state machine, the console bootstrap, invites, redemption, approval and the operator's
    other decisions), ``hivemind.entrance.reducer`` and ``hivemind.entrance.store`` (the Entrance
    tables). Imports only ``hivemind.common.errors``.

Key invariants:
    - Every class sets its own ``code``; no two share one (a test walks them all).
    - No message ever carries a password, a key, an invite code or a session token: messages name
      devices, statuses and invites by id or hash only (codingrules sections 12 and 13).
    - ``PasskeyRejectedError`` and ``KeyUnwrapError`` never say which check failed beyond what the
      verifying library reports about the ceremony itself: a wrong password and a tampered blob
      read the same (ADR-0041: a failure never says which factor failed).
    - ``EnrolmentRefusedError``, ``ChallengeRejectedError`` and ``AuthenticationFailedError``
      carry one fixed message each, so a device that is refused learns nothing about why (an
      unknown, used or expired code, a bad proof, a wrong password, a replayed nonce all read the
      same); the reason goes to the Pheromone Trail instead.
    - Statuses are typed as ``Enum`` here, not ``DeviceStatus``, so this module never imports
      ``hivemind.entrance.enrol.state``, which imports it.

See Also:
    - .claude/codingrules.md section 10 for the error rules this module follows.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for what is refused.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import (
    ConfigurationError,
    ConflictError,
    HiveMindError,
    NotFoundError,
    PermissionDeniedError,
)

__all__ = [
    "AuthenticationFailedError",
    "BreakGlassRefusedError",
    "CapabilityCeilingError",
    "CellNotFoundError",
    "CertificateNotIssuedError",
    "ChallengeRejectedError",
    "ConfirmationRefusedError",
    "ConsoleProtectedError",
    "DeviceAlreadyExistsError",
    "DeviceNotFoundError",
    "DeviceStatusConflictError",
    "EnrolmentRefusedError",
    "EntranceError",
    "EntranceModeConflictError",
    "InvalidApprovalError",
    "InvalidDeviceEntryError",
    "InvalidDeviceTransitionError",
    "InvalidModeTransitionError",
    "InvalidPendingTransitionError",
    "InviteAlreadyExistsError",
    "InviteAlreadyUsedError",
    "InviteExpiredError",
    "InviteNotFoundError",
    "KeyUnwrapError",
    "OperatorAlreadyInitialisedError",
    "OperatorNotInitialisedError",
    "OperatorPasswordMismatchError",
    "PasskeyRejectedError",
    "PendingNotFoundError",
    "PendingStatusConflictError",
    "ReopenRefusedError",
    "SingleOperatorError",
    "StepUpUnavailableError",
    "StewardGrantError",
    "TravelLockUnavailableError",
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


class ChallengeRejectedError(EntranceError, PermissionDeniedError):
    """Raise when a ceremony answers a challenge the Entrance cannot accept, whatever the reason.

    Unknown, already spent, expired, or issued for another subject or binding key: all read the
    same, so the error is never an oracle for which it was.
    """

    code: ClassVar[str] = "hivemind.entrance.challenge_rejected"

    def __init__(self) -> None:
        """Build the error; it has one fixed message on purpose (see the class docstring)."""
        super().__init__(
            "The challenge is unknown, spent, expired or was issued for another ceremony; ask "
            "for a new one."
        )


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


class SingleOperatorError(EntranceError, ConflictError):
    """Raise when a second operator is asked for: this Brood keeps one operator credential."""

    code: ClassVar[str] = "hivemind.entrance.single_operator"


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


class CertificateNotIssuedError(EntranceError, NotFoundError):
    """Raise when a device asks for its client certificate and holds none.

    Nothing was issued (it sent no certificate request, the Hive runs no authority, or it is a
    browser whose bundle went to the operator), or it was withdrawn when the device left.
    """

    code: ClassVar[str] = "hivemind.entrance.certificate_not_issued"

    def __init__(self, device_id: str) -> None:
        """Build the error for one device.

        Args:
            device_id: The device that asked.
        """
        super().__init__(
            f"Device {device_id} holds no client certificate: none was issued at its approval."
        )
        self.device_id = device_id


class CellNotFoundError(EntranceError, NotFoundError):
    """Raise when no Cell the Hive knows (attached or tracked by the lifecycle) has the id."""

    code: ClassVar[str] = "hivemind.entrance.cell_not_found"

    def __init__(self, cell_id: str) -> None:
        """Build the error for a Cell no Warden supervises and no lifecycle tracks.

        Args:
            cell_id: The id that was looked up.
        """
        super().__init__(f"No Cell {cell_id} is attached to the Queen or tracked as Virtual.")
        self.cell_id = cell_id


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
    loopback-bound console is recorded straight as APPROVED (ADR-0041). Anything else would skip
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


# ──────────────────────────────────────────────────────────────────────────────
# Enrolment: redemption, approval and the operator's other decisions
# ──────────────────────────────────────────────────────────────────────────────


class EnrolmentRefusedError(EntranceError, PermissionDeniedError):
    """Raise when an invite cannot be redeemed, whatever the reason.

    An unknown, used or expired code, a device no longer waiting for its key, and a bad key proof
    all raise this with one fixed message: the redeeming device is unauthenticated, and telling it
    which check failed would let it probe invites. The reason is recorded on the Pheromone Trail
    as ``guard.entrance_redeem_failed`` instead.
    """

    code: ClassVar[str] = "hivemind.entrance.enrolment_refused"

    def __init__(self) -> None:
        """Build the error; it has one fixed message on purpose (see the class docstring)."""
        super().__init__(
            "The invite could not be redeemed; ask the operator for a new one at the Hive Stand."
        )


class CapabilityCeilingError(EntranceError, PermissionDeniedError):
    """Raise when an approval names a capability the ``device`` role's ceiling does not allow."""

    code: ClassVar[str] = "hivemind.entrance.capability_beyond_ceiling"

    def __init__(self, capability: str) -> None:
        """Build the error for the first capability beyond the ceiling.

        Args:
            capability: The offending capability string, as the approval named it.
        """
        super().__init__(
            f"The capability {capability!r} is beyond the device ceiling ([guard] roles.device "
            "allow); an approval can never grant it."
        )
        self.capability = capability


class InvalidApprovalError(EntranceError, ConflictError):
    """Raise when an approval cannot bind what it asks for, for instance an expiry already past."""

    code: ClassVar[str] = "hivemind.entrance.invalid_approval"


class StewardGrantError(EntranceError, PermissionDeniedError):
    """Raise when a steward device asks to grant more than it may (ADR-0041's steward rule)."""

    code: ClassVar[str] = "hivemind.entrance.steward_grant_refused"

    def __init__(self, steward_id: str, capability: str | None, reason: str) -> None:
        """Build the error for one refused steward grant.

        Args:
            steward_id: The steward device's id.
            capability: The offending capability string, or None when the steward itself may
                not approve at all.
            reason: Why, as a clause, e.g. ``"the steward does not hold it"``.
        """
        subject = f"grant {capability!r}" if capability is not None else "approve devices"
        super().__init__(f"Steward device {steward_id} may not {subject}: {reason}.")
        self.steward_id = steward_id
        self.capability = capability


class ConsoleProtectedError(EntranceError, PermissionDeniedError):
    """Raise when an operation would revoke or expire the Hive Stand's own console.

    The console can be locked and unlocked like any other device, but losing it means resetting
    the operator password with ``hive entrance operator password --reset`` (ADR-0041), so nothing
    else may take it away.
    """

    code: ClassVar[str] = "hivemind.entrance.console_protected"

    def __init__(self, device_id: str, action: str) -> None:
        """Build the error for a protected console.

        Args:
            device_id: The console's device id.
            action: What was refused, as a past participle, e.g. ``"revoked"``.
        """
        super().__init__(
            f"Device {device_id} is the Hive Stand console and cannot be {action}; replacing it "
            "means `hive entrance operator password --reset` while `hive serve` is stopped."
        )
        self.device_id = device_id


# ──────────────────────────────────────────────────────────────────────────────
# Login, sessions and step-up
# ──────────────────────────────────────────────────────────────────────────────


class AuthenticationFailedError(EntranceError, PermissionDeniedError):
    """Raise when a login, a signed request, a socket's first frame or a step-up is refused.

    An unknown device, a device that is not approved, a bad device proof, a wrong password, an
    unknown, ended or idle session, a bad signature, a stale timestamp, a replayed nonce and the
    wrong listener all raise this with one fixed message (ADR-0041: a failure never says which
    factor failed); the reason category goes to the Pheromone Trail instead.
    """

    code: ClassVar[str] = "hivemind.entrance.authentication_failed"

    def __init__(self) -> None:
        """Build the error; it has one fixed message on purpose (see the class docstring)."""
        super().__init__("Authentication failed; log in again with the device key and password.")


class StepUpUnavailableError(EntranceError, PermissionDeniedError):
    """Raise when a device no person types at asks to step up.

    A non-interactive device (a program key) cannot step up (ADR-0041): what it asks for that
    needs step-up waits as a pending confirmation until a person confirms it from an interactive
    device.
    """

    code: ClassVar[str] = "hivemind.entrance.step_up_unavailable"

    def __init__(self, device_id: str) -> None:
        """Build the error for a non-interactive device.

        Args:
            device_id: The device that asked to step up.
        """
        super().__init__(
            f"Device {device_id} is not interactive and cannot step up; a person confirms its "
            "request from an interactive device instead."
        )
        self.device_id = device_id


class BreakGlassRefusedError(EntranceError, PermissionDeniedError):
    """Raise when a break-glass action lacks its phrase, its step-up or an interactive device.

    Absconding, Sting Cut and Supersedure need the typed confirmation phrase of codingrules 15 on
    every path, the API included, and only from a device a person types at (ADR-0041).
    """

    code: ClassVar[str] = "hivemind.entrance.break_glass_refused"


class ConfirmationRefusedError(EntranceError, PermissionDeniedError):
    """Raise when a pending confirmation cannot be held, confirmed or cancelled; says why."""

    code: ClassVar[str] = "hivemind.entrance.confirmation_refused"


class PendingNotFoundError(EntranceError, NotFoundError):
    """Raise when no pending confirmation has the requested id."""

    code: ClassVar[str] = "hivemind.entrance.pending_not_found"

    def __init__(self, pending_id: str) -> None:
        """Build the error for a missing pending confirmation.

        Args:
            pending_id: The id that was looked up.
        """
        super().__init__(f"No pending confirmation {pending_id} exists in the Entrance tables.")
        self.pending_id = pending_id


class InvalidPendingTransitionError(EntranceError, ConflictError):
    """Raise when a pending confirmation's status change is not an edge of its state machine."""

    code: ClassVar[str] = "hivemind.entrance.invalid_pending_transition"

    def __init__(self, from_status: Enum, to_status: Enum) -> None:
        """Build the error for a forbidden edge.

        Args:
            from_status: The status the confirmation is in (or is expected to be in).
            to_status: The status a caller asked to move it to.
        """
        super().__init__(
            f"Cannot move a pending confirmation from {from_status.name} to {to_status.name}: "
            "a confirmation is settled once, from PENDING."
        )
        self.from_status = from_status
        self.to_status = to_status


class PendingStatusConflictError(EntranceError, ConflictError):
    """Raise when a pending confirmation is not in the status its settler expected.

    Two people confirming the same request at once can never both carry it out: the second
    finds it settled and fails.
    """

    code: ClassVar[str] = "hivemind.entrance.pending_status_conflict"

    def __init__(self, pending_id: str, expected: Enum, actual: Enum) -> None:
        """Build the error for a stale expectation.

        Args:
            pending_id: The confirmation whose status moved.
            expected: What the caller believed the status was.
            actual: What the Entrance tables hold.
        """
        super().__init__(
            f"Pending confirmation {pending_id} is {actual.name}, not {expected.name}; it was "
            "settled already."
        )
        self.pending_id = pending_id
        self.expected = expected
        self.actual = actual


# ──────────────────────────────────────────────────────────────────────────────
# The travel lock and the Entrance Reducer
# ──────────────────────────────────────────────────────────────────────────────


class TravelLockUnavailableError(EntranceError, ConfigurationError):
    """Raise when ``travel_lock`` is on but the Entrance cannot see real endpoints.

    The travel lock works only with ``expose = "vpn"`` on Tailscale, whose local API reports
    each peer's current endpoint (ADR-0041); anywhere else it refuses to start rather than run
    blind.
    """

    code: ClassVar[str] = "hivemind.entrance.travel_lock_unavailable"

    def __init__(self, reason: str) -> None:
        """Build the error with why endpoints cannot be seen.

        Args:
            reason: A clause, e.g. ``"expose is 'lan', not 'vpn'"``.
        """
        super().__init__(
            f"travel_lock is on but the Entrance cannot see where devices connect from: {reason}. "
            "Turn travel_lock off or run expose = 'vpn' on Tailscale."
        )
        self.reason = reason


class InvalidModeTransitionError(EntranceError, ConflictError):
    """Raise when an Entrance mode change is not an edge of ``OPEN <-> REDUCED``."""

    code: ClassVar[str] = "hivemind.entrance.invalid_mode_transition"

    def __init__(self, from_mode: Enum, to_mode: Enum) -> None:
        """Build the error for a forbidden edge.

        Args:
            from_mode: The mode the Entrance is in (or is expected to be in).
            to_mode: The mode a caller asked to move it to.
        """
        super().__init__(
            f"Cannot move the Entrance from {from_mode.name} to {to_mode.name}: only OPEN to "
            "REDUCED and back are edges."
        )
        self.from_mode = from_mode
        self.to_mode = to_mode


class EntranceModeConflictError(EntranceError, ConflictError):
    """Raise when the Entrance is not in the mode a caller expected when it asked to move it."""

    code: ClassVar[str] = "hivemind.entrance.mode_conflict"

    def __init__(self, expected: Enum, actual: Enum) -> None:
        """Build the error for a stale expectation.

        Args:
            expected: What the caller believed the mode was.
            actual: What the Entrance tables hold.
        """
        super().__init__(
            f"The Entrance is {actual.name}, not {expected.name}; re-read the mode and decide "
            "again."
        )
        self.expected = expected
        self.actual = actual


class ReopenRefusedError(EntranceError, PermissionDeniedError):
    """Raise when anything but a stepped-up loopback session asks to reopen a reduced Entrance.

    Reopening is a loopback-only decision that needs step-up (ADR-0041).
    """

    code: ClassVar[str] = "hivemind.entrance.reopen_refused"
