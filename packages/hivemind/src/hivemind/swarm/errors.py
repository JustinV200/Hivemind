"""Define SwarmError and the ways enrolling, linking to or driving a Swarm device can fail.

The Swarm is the mesh of enrolled devices (existing machines the Hive borrows as Real Cells, and
leaves exactly as found), each reached through a Pollen Packet (the thin gateway installed on it)
that dials the Hive Stand (the machine the Queen, the central orchestrator, runs on). This module
is the package's own error tree, rooted at `SwarmError`, so a caller several layers up can catch
one name and know it caught anything the Swarm raised on purpose (codingrules section 10). Most
classes also descend from one of `hivemind.common.errors`' base categories (a missing node is a
`NotFoundError`, a stale compare-and-set a `ConflictError`, a denied command a
`PermissionDeniedError`), and the ones a `CellSession` caller meets mid-command (the link is gone,
the device did not answer) are also `CellError`s, so the code that already handles a failed Cell
operation handles these too. Every `code` is `hive.swarm.<name>`: it is the stable string a
`control.error` carries across Waggle, and `EnrolmentDeniedError`'s is the one the enrolment
ceremony names (docs/waggle/enrolment.md section 6). `EnrolRefusal` lists why a door says no; it
travels in the trail event `swarm.enrol_refused` and never carries the invite token.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Raised by every other module in
    this package and read by the Queen's swarm gate (Layer 6), a device's Warden (Layer 5) and the
    `hive swarm` commands (Layer 7). Calls into hivemind.cell.errors and hivemind.common.errors
    only.

Key invariants:
    - Every SwarmError subclass sets its own `code`, and none shares one with another.
    - No message carries an invite token, a key, a command's arguments, a path or file content:
      only identifiers, counts and check names.
    - `DeviceRequestError.device_code` is the code the device's `control.error` carried, verbatim,
      so a caller can branch on it without parsing a sentence.

See Also:
    - .claude/codingrules.md section 10 for the exceptions rules this module follows.
    - docs/waggle/enrolment.md section 6 for the denial the device sees.
    - docs/waggle/gateway-contract.md section 15 for the codes a device answers with.
    - hivemind.cell.errors for CellError and LeaseRefusedError, which several classes extend.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum, StrEnum
from typing import ClassVar

from hivemind.cell.errors import CellError, LeaseRefusedError
from hivemind.common.errors import (
    ConflictError,
    DeadlineExceededError,
    HiveMindError,
    NotFoundError,
    PermissionDeniedError,
)

__all__ = [
    "CommandDeniedError",
    "DeviceRequestError",
    "DeviceTimeoutError",
    "EnrolRefusal",
    "EnrolmentDeniedError",
    "GatewayUnavailableError",
    "InvalidNodeTransitionError",
    "InviteConflictError",
    "InviteFormatError",
    "InviteNotFoundError",
    "InviteRefusedError",
    "LadderError",
    "NodeNotFoundError",
    "NodeRevokedError",
    "NodeStatusConflictError",
    "PacketError",
    "ShieldUnverifiedError",
    "SwarmError",
    "TransferIntegrityError",
]


class EnrolRefusal(StrEnum):
    """Why the enrolment door said no; the reason code `swarm.enrol_refused` records."""

    UNKNOWN_INVITE = "unknown_invite"  # No invite has the presented token's hash.
    EXPIRED = "expired"  # The invite's time to live lapsed before it was presented.
    REVOKED = "revoked"  # The operator cancelled the invite.
    USED = "used"  # The invite already admitted a device: single use.
    IDENTITY_MISMATCH = "identity_mismatch"  # The request's ids disagree with its envelope's.
    IDENTITY_IN_USE = "identity_in_use"  # The device, node, Cell or key is already enrolled.
    WRONG_HIVE = "wrong_hive"  # The request was addressed to another Hive's Queen.
    NOT_AN_ENROLMENT = "not_an_enrolment"  # The first frame was not a `swarm.enrol_request`.


class SwarmError(HiveMindError):
    """Root of every error `hivemind.swarm` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hive.swarm.error"


class EnrolmentDeniedError(SwarmError, PermissionDeniedError):
    """Raise when the enrolment door refuses a device; nothing was pinned, nothing was created.

    Its `code` is the one the packet sees in the `control.error` correlated to its request.
    """

    code: ClassVar[str] = "hive.swarm.enrol_denied"

    def __init__(self, reason: EnrolRefusal, device_id: str, node_id: str) -> None:
        """Build the error for one refused enrolment request.

        Args:
            reason: Why the door refused; a closed set, so the trail records a code, not prose.
            device_id: The `device_id` the request claimed, named so the refusal is debuggable.
            node_id: The `node_id` the request claimed.
        """
        super().__init__(
            f"Enrolment of device {device_id} (node {node_id}) was refused: {reason.value}. "
            "Nothing was created; mint a new invite and enrol again."
        )
        self.reason = reason
        self.device_id = device_id
        self.node_id = node_id


class InviteRefusedError(SwarmError, PermissionDeniedError):
    """Raise when an invite cannot be minted as asked, e.g. for the virtual-only Night Veil tier."""

    code: ClassVar[str] = "hive.swarm.invite_refused"


class InviteFormatError(SwarmError, ValueError):
    """Raise when text is not an invite string (`pollen1.<base64url(JSON)>`) the format allows.

    A ValueError too, so a caller that parses an operator-typed string handles it as bad input.
    The message names what is wrong and never repeats the text, which is a secret until used.
    """

    code: ClassVar[str] = "hive.swarm.invite_format"


class InviteNotFoundError(SwarmError, NotFoundError):
    """Raise when a registry lookup or revocation names an invite that does not exist."""

    code: ClassVar[str] = "hive.swarm.invite_not_found"

    def __init__(self, code_hash: str) -> None:
        """Build the error for an unknown invite.

        Args:
            code_hash: The SHA-256 of the token asked about; never the token itself.
        """
        super().__init__(f"No Swarm invite has the token hash {code_hash[:12]}...")
        self.code_hash = code_hash


class InviteConflictError(SwarmError, ConflictError):
    """Raise when an invite write would break its machine: a duplicate, or a forbidden edge."""

    code: ClassVar[str] = "hive.swarm.invite_conflict"


class NodeNotFoundError(SwarmError, NotFoundError):
    """Raise when a registry write names a device that is not enrolled."""

    code: ClassVar[str] = "hive.swarm.node_not_found"

    def __init__(self, device_id: str) -> None:
        """Build the error for an unknown device.

        Args:
            device_id: The `DeviceId` that matched no registry row.
        """
        super().__init__(f"No device {device_id} is enrolled in the Swarm registry.")
        self.device_id = device_id


class NodeRevokedError(SwarmError, PermissionDeniedError):
    """Raise when an operation targets a revoked node, whose enrolment is final."""

    code: ClassVar[str] = "hive.swarm.node_revoked"

    def __init__(self, device_id: str) -> None:
        """Build the error for an operation on a revoked device.

        Args:
            device_id: The revoked device.
        """
        super().__init__(
            f"Device {device_id} is revoked; its key is refused and it can only be replaced by "
            "enrolling a new device with a new invite."
        )
        self.device_id = device_id


class NodeStatusConflictError(SwarmError, ConflictError):
    """Raise when a compare-and-set of a node's status finds it is no longer what was expected."""

    code: ClassVar[str] = "hive.swarm.node_status_conflict"

    def __init__(self, device_id: str, expected: Enum, found: Enum) -> None:
        """Build the error for a status that moved under the caller.

        Args:
            device_id: The device whose status was being changed.
            expected: The status the caller believed it had.
            found: The status the registry actually holds.
        """
        super().__init__(
            f"Device {device_id} is {found.name}, not the {expected.name} the caller expected; "
            "read it again before changing its status."
        )
        self.device_id = device_id
        self.expected = expected
        self.found = found


class InvalidNodeTransitionError(SwarmError, ConflictError):
    """Raise when `hivemind.swarm.node_state` is asked for an edge its table does not have."""

    code: ClassVar[str] = "hive.swarm.invalid_node_transition"

    def __init__(self, from_state: Enum, to_state: Enum, device_id: str | None = None) -> None:
        """Build the error for a forbidden node-status edge.

        Args:
            from_state: The status the node is in.
            to_state: The status a caller asked to move it to.
            device_id: The node's device, when the caller has it, folded into the message.
        """
        subject = f" for device {device_id}" if device_id is not None else ""
        super().__init__(
            f"Cannot transition{subject} from {from_state.name} to {to_state.name}: no such "
            "edge exists in the node status machine."
        )
        self.from_state = from_state
        self.to_state = to_state
        self.device_id = device_id


class LadderError(SwarmError, ConflictError):
    """Raise when a rung change breaks the runtime ladder: only one rung at a time is allowed."""

    code: ClassVar[str] = "hive.swarm.ladder_refused"

    def __init__(self, from_level: Enum, to_level: Enum, device_id: str | None = None) -> None:
        """Build the error for a forbidden rung change.

        Args:
            from_level: The rung the device is on.
            to_level: The rung a caller asked for.
            device_id: The device, when the caller has it, folded into the message.
        """
        subject = f" for device {device_id}" if device_id is not None else ""
        super().__init__(
            f"Cannot move{subject} from {from_level.name} to {to_level.name}: the Warden moves "
            "one rung at a time (GATEWAY <-> COLONIZED <-> NUC)."
        )
        self.from_level = from_level
        self.to_level = to_level
        self.device_id = device_id


class PacketError(SwarmError):
    """Raise when `hive swarm packet` cannot build a Pollen Packet; nothing minted is left live.

    The message names the step that failed (a missing build, an unset or unreadable OAuth source,
    Tailscale refusing a key, a folder that already exists) and never carries a secret.
    """

    code: ClassVar[str] = "hive.swarm.packet_failed"


class GatewayUnavailableError(SwarmError, CellError):
    """Raise when a device's link is absent or ended while a caller needed it.

    It is a `CellError` too: a Worker or Undertaker that already handles a failed Cell operation
    handles a lost device the same way, and a lease left to the device's dead-man switch is
    ORPHANED by `RealCellLease.release` so a sweep can retry it.
    """

    code: ClassVar[str] = "hive.swarm.gateway_unavailable"


class DeviceTimeoutError(SwarmError, CellError, DeadlineExceededError):
    """Raise when a connected device did not answer a request within its deadline."""

    code: ClassVar[str] = "hive.swarm.device_timeout"


class DeviceRequestError(SwarmError, CellError):
    """Raise when a device answers a request with a `control.error`.

    `device_code` is that error's stable code (`pollen.not_found`, `pollen.capability_denied`,
    ...), so a caller maps it without parsing the sentence; `hivemind.swarm.session.codes` turns
    the ones a `CellSession` contract names into the `hivemind.cell` errors it promises.
    """

    code: ClassVar[str] = "hive.swarm.device_request_failed"

    def __init__(self, device_code: str, message: str, *, is_retryable: bool = False) -> None:
        """Build the error for one refused request.

        Args:
            device_code: The `control.error` code the device sent.
            message: The device's own sentence; it names ids, never a path or content.
            is_retryable: Whether the device said resending the request may succeed.
        """
        super().__init__(f"The device refused the request with {device_code}: {message}")
        self.device_code = device_code
        self.device_message = message
        self.is_retryable = is_retryable


class TransferIntegrityError(SwarmError, CellError):
    """Raise when a file transfer's length or digest does not match what the device reported."""

    code: ClassVar[str] = "hive.swarm.transfer_corrupt"


class CommandDeniedError(SwarmError, PermissionDeniedError):
    """Raise when the Guard refuses a command before it is sent to a device."""

    code: ClassVar[str] = "hive.swarm.command_denied"

    def __init__(self, lease_id: str, needed: str) -> None:
        """Build the error for a refused device command.

        Args:
            lease_id: The lease the command would have run in.
            needed: The capability the command needed, e.g. `exec:ls`; it names a program or a
                path scope, never an argument or content.
        """
        super().__init__(
            f"The Guard denied the command on lease {lease_id}: it needs {needed}, which the "
            "Warden does not hold at this point. Nothing was sent to the device."
        )
        self.lease_id = lease_id
        self.needed = needed


class ShieldUnverifiedError(SwarmError, LeaseRefusedError):
    """Raise when a PROPOLIS device's tunnel checks fail, after its lease was released at once."""

    code: ClassVar[str] = "hive.swarm.shield_unverified"

    def __init__(self, cell_id: str, failed_checks: Sequence[str]) -> None:
        """Build the error for a Propolis tier the device's own tunnel does not back.

        Args:
            cell_id: The Cell the lease was refused on.
            failed_checks: The names of the checks that failed, e.g. `default_route`.
        """
        names = ", ".join(failed_checks) or "none ran"
        super().__init__(
            cell_id,
            f"its Propolis tunnel is not verified (failed checks: {names}); the lease was "
            "released and nothing was placed on the device",
        )
        self.failed_checks = tuple(failed_checks)
