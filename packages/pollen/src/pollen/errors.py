"""Define the pollen error tree: PollenError at the root, one subclass per stable code.

The Pollen Packet (the thin gateway program on a device the Hive borrows) refuses a request by
answering the Queen (the Hive's central orchestrator) or the Warden (the per-Cell supervisor)
with a ``control.error`` message whose ``code`` is stable and whose text never carries a path, a
hostname or content, because anything on a Real Cell (an existing device borrowed for a task) is
the highest sensitivity label. The ten codes of gateway-contract section 15 each have a class
here, so executors raise a typed error and the dispatcher turns it into the wire reply without a
lookup table; the classes after them are local failures (a bad invite, a state file that cannot
be read, a protocol major the packet cannot speak) that stay on the device, except
``InternalError``, which is what the dispatcher reports when a request fails for a reason of the
packet's own. The root is its own, not the Hive's ``HiveMindError``, because pollen may import
nothing from ``hivemind`` (codingrules sections 4 and 10).

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Raised by the executors, the
    lease code, the enrolment ceremony and the platform shims; caught by pollen.agent.dispatch,
    which answers the sender, and by pollen.cli, which prints the sentence and exits.

Key invariants:
    - Every class sets its own ``code``; no two share one (tests/test_errors.py checks it).
    - A code that has been sent on the wire is never renamed.
    - Messages are full sentences that name ids and counts and never a path, hostname, argument
      value or file content.

See Also:
    - docs/waggle/gateway-contract.md section 15 for the table these classes mirror.
    - waggle.errors for the equivalent root on the protocol side.
"""

from __future__ import annotations

from typing import ClassVar

__all__ = [
    "WIRE_CODES",
    "CapabilityDeniedError",
    "EnrolmentDeniedError",
    "EnrolmentError",
    "InternalError",
    "InvalidRequestError",
    "InvalidTransitionError",
    "InviteError",
    "LeaseNotOpenError",
    "LeaseRefusedError",
    "LinkLostError",
    "NotEnrolledError",
    "NotFoundError",
    "PathRefusedError",
    "PlatformError",
    "PollenError",
    "ProtocolMismatchError",
    "QuotaExceededError",
    "StartFailedError",
    "StateError",
    "TransferRefusedError",
    "UnsupportedError",
]


class PollenError(Exception):
    """Root of every error pollen raises on purpose."""

    #: Dotted, lowercase, stable identifier; the ``code`` of the control.error a refusal sends.
    code: ClassVar[str] = "pollen.error"
    #: Whether resending the same request may succeed; false for every refusal pollen makes.
    is_retryable: ClassVar[bool] = False


# ──────────────────────────────────────────────────────────────────────────────
# The ten codes of gateway-contract section 15 (they cross the wire)
# ──────────────────────────────────────────────────────────────────────────────


class CapabilityDeniedError(PollenError):
    """A request is not covered by the capabilities the node was granted."""

    code: ClassVar[str] = "pollen.capability_denied"


class PathRefusedError(PollenError):
    """A path resolves outside scratch and the lease's reach, or cannot be resolved safely."""

    code: ClassVar[str] = "pollen.path_refused"


class NotFoundError(PollenError):
    """A file or a process the request names does not exist or was not started by this lease."""

    code: ClassVar[str] = "pollen.not_found"


class StartFailedError(PollenError):
    """A background program could not be started."""

    code: ClassVar[str] = "pollen.start_failed"


class QuotaExceededError(PollenError):
    """A write would pass the scratch quota or the free disk, or a change could not be recorded."""

    code: ClassVar[str] = "pollen.quota_exceeded"


class TransferRefusedError(PollenError):
    """A file transfer broke a rule: order, length, digest, size cap or too many open transfers."""

    code: ClassVar[str] = "pollen.transfer_refused"


class LeaseRefusedError(PollenError):
    """A cell.request or cell.teardown_request cannot be honoured (another Cell, another holder)."""

    code: ClassVar[str] = "pollen.lease_refused"


class UnsupportedError(PollenError):
    """A kind the packet recognises but does not implement, or one this platform cannot run."""

    code: ClassVar[str] = "pollen.unsupported"


class LeaseNotOpenError(PollenError):
    """Session or teardown traffic names a lease the packet does not hold open."""

    code: ClassVar[str] = "hive.session.lease_not_open"


class InvalidRequestError(PollenError):
    """A frame had a readable id but a payload or envelope rule failed."""

    code: ClassVar[str] = "waggle.codec.invalid_payload"


# ──────────────────────────────────────────────────────────────────────────────
# Local failures (they never cross the wire)
# ──────────────────────────────────────────────────────────────────────────────


class InviteError(PollenError):
    """The invite string is malformed, expired or not one this packet may use."""

    code: ClassVar[str] = "pollen.invite.invalid"


class EnrolmentError(PollenError):
    """The enrolment ceremony failed before the device held a complete enrolment."""

    code: ClassVar[str] = "pollen.enrol.failed"


class EnrolmentDeniedError(EnrolmentError):
    """The Queen answered the enrolment request with hive.swarm.enrol_denied."""

    code: ClassVar[str] = "hive.swarm.enrol_denied"


class NotEnrolledError(PollenError):
    """There is no enrolment in the data directory: ``pollen enrol`` has not succeeded here."""

    code: ClassVar[str] = "pollen.state.not_enrolled"


class StateError(PollenError):
    """A file in the data directory cannot be read, parsed or written."""

    code: ClassVar[str] = "pollen.state.unreadable"


class ProtocolMismatchError(PollenError):
    """The Queen speaks a Waggle major this packet does not, and no replay can fix that."""

    code: ClassVar[str] = "pollen.protocol.mismatch"


class PlatformError(PollenError):
    """The host's service manager or process model refused something pollen asked of it."""

    code: ClassVar[str] = "pollen.platform.failed"


class InternalError(PollenError):
    """Something failed inside the packet while it handled a request; the request did not run."""

    code: ClassVar[str] = "pollen.internal_error"


class InvalidTransitionError(PollenError):
    """A lease was asked to take a state edge its transition table does not allow."""

    code: ClassVar[str] = "pollen.lease.invalid_transition"


class LinkLostError(PollenError):
    """A connection to the Queen ended soon after it began, so the next dial must back off."""

    code: ClassVar[str] = "pollen.link.lost"


# The codes a refusal may carry on the wire: the ten of gateway-contract section 15, plus the
# internal-error code the dispatcher uses when a request fails for a reason of the packet's own
# (a local class not in this set is reported as that, never under its own code).
WIRE_CODES = frozenset(
    cls.code
    for cls in (
        CapabilityDeniedError,
        PathRefusedError,
        NotFoundError,
        StartFailedError,
        QuotaExceededError,
        TransferRefusedError,
        LeaseRefusedError,
        UnsupportedError,
        LeaseNotOpenError,
        InvalidRequestError,
        InternalError,
    )
)
