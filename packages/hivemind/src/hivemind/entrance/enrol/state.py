"""Define DeviceStatus and the one transition table every enrolled device's lifecycle follows.

An enrolled device is a client of the Hive Entrance (a browser, a phone, the CLI, a program, the
Hive Stand's own console; the Hive Stand is the machine the Queen, the orchestrator, runs on) that
holds its own key (ADR-0033). The Hive Entrance is the Hive's one HTTP door. This module is its
state machine in the shape codingrules section 9 requires: one ``Enum`` and one table, each edge
commented with who takes it and carrying the Pheromone Trail (audit log) event kind it is recorded
as, tested edge by edge (codingrules Appendix C, the "Enrolled device" row). A device is created
INVITED when an invite is minted on loopback (``guard.entrance_invited``); only the Hive Stand
console is created APPROVED, by the operator bootstrap. Nothing else decides whether a status change
is legal: ``hivemind.entrance.store`` calls ``assert_transition`` inside every status change it
makes.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Read by the
    Entrance tables (``hivemind.entrance.store``) before every status change and, later, by the
    enrolment, lockout and revocation routes for the event kind each change records. Calls into
    ``hivemind.entrance.errors`` only.

Key invariants:
    - ``TRANSITIONS`` has exactly one entry per ``DeviceStatus``; the terminal statuses (DENIED,
      EXPIRED, REVOKED) map to no edges, so a device that leaves for good never comes back.
    - Every edge names its ``guard.entrance_*`` trail kind: the new status lowercased, except
      LOCKED to APPROVED, which is ``guard.entrance_unlocked``.
    - Approval, denial, unlock and revocation are loopback-only decisions (Appendix C, ADR-0033);
      the routes enforce that, and the table records it in the edge comments.
    - The trail kinds are referenced as strings: the ``guard`` event family that declares them is
      ``hivemind.pheromone``'s, and this module never imports it.

See Also:
    - .claude/codingrules.md section 9 and Appendix C for the shape and the row this implements.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for every edge.
    - hivemind.entrance.store.protocol for the one place status changes are applied.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.entrance.errors import InvalidDeviceTransitionError

INVITED_TRAIL_KIND = "guard.entrance_invited"  # Recorded when a device record is first created.

__all__ = [
    "INVITED_TRAIL_KIND",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "DeviceStatus",
    "assert_transition",
    "can_transition",
    "is_terminal",
    "trail_kind",
]


class DeviceStatus(Enum):
    """Every state an enrolled device can be in; see TRANSITIONS for the moves between them."""

    INVITED = "INVITED"  # An invite was minted on loopback; nothing has presented it yet.
    PENDING = "PENDING"  # The device redeemed the invite with its key; awaiting the operator.
    APPROVED = "APPROVED"  # May log in, within its capabilities, spend cap and expiry.
    DENIED = "DENIED"  # Terminal: the operator refused the request.
    EXPIRED = "EXPIRED"  # Terminal: an invite, a request or an approval ran out of time.
    LOCKED = "LOCKED"  # Too many failed logins or denials; only a loopback unlock reopens it.
    REVOKED = "REVOKED"  # Terminal: the operator withdrew the invite or the device.


# The three statuses TRANSITIONS maps to no edges; cross-checked against the table by a test
# rather than derived from it, so a table edit that strands a status fails loudly.
TERMINAL_STATUSES: frozenset[DeviceStatus] = frozenset(
    {DeviceStatus.DENIED, DeviceStatus.EXPIRED, DeviceStatus.REVOKED}
)

# The single transition table (codingrules section 9): from-status -> {to-status: trail kind}.
# Every edge says who takes it; nothing outside this table makes a status change legal.
TRANSITIONS: Mapping[DeviceStatus, Mapping[DeviceStatus, str]] = {
    DeviceStatus.INVITED: {
        # The device presented the invite with its public key and a self-description.
        DeviceStatus.PENDING: "guard.entrance_pending",
        # The invite lapsed unredeemed (invite_ttl_minutes); the expiry sweep takes this edge.
        DeviceStatus.EXPIRED: "guard.entrance_expired",
        # The operator cancelled the unredeemed invite on loopback.
        DeviceStatus.REVOKED: "guard.entrance_revoked",
    },
    DeviceStatus.PENDING: {
        # The operator approved it on loopback (or a steward device after full step-up),
        # binding name, capabilities, spend cap, expiry and interactivity.
        DeviceStatus.APPROVED: "guard.entrance_approved",
        # The operator refused it on loopback.
        DeviceStatus.DENIED: "guard.entrance_denied",
        # Nobody decided within pending_ttl_hours; the expiry sweep takes this edge.
        DeviceStatus.EXPIRED: "guard.entrance_expired",
    },
    DeviceStatus.APPROVED: {
        # Lockout: lockout_attempts valid-proof login failures, or lockout_denials capability
        # denials in the window; or an interactive device locked it after step-up (a lost phone).
        DeviceStatus.LOCKED: "guard.entrance_locked",
        # The approval's own expiry passed; the expiry sweep takes this edge.
        DeviceStatus.EXPIRED: "guard.entrance_expired",
        # The operator revoked it on loopback.
        DeviceStatus.REVOKED: "guard.entrance_revoked",
    },
    DeviceStatus.LOCKED: {
        # The operator unlocked it on loopback (the console: offline, with the password).
        DeviceStatus.APPROVED: "guard.entrance_unlocked",
        # The approval's expiry passed while it was locked.
        DeviceStatus.EXPIRED: "guard.entrance_expired",
        # The operator revoked it on loopback.
        DeviceStatus.REVOKED: "guard.entrance_revoked",
    },
    DeviceStatus.DENIED: {},  # Terminal: a denied device enrols again only with a new invite.
    DeviceStatus.EXPIRED: {},  # Terminal: an expired device enrols again only with a new invite.
    DeviceStatus.REVOKED: {},  # Terminal: a revoked device enrols again only with a new invite.
}


def can_transition(from_status: DeviceStatus, to_status: DeviceStatus) -> bool:
    """Return whether TRANSITIONS has the edge ``from_status`` to ``to_status``.

    Args:
        from_status: The device's current status.
        to_status: The status a caller wants to move it to.

    Returns:
        True when the edge exists.
    """
    return to_status in TRANSITIONS[from_status]


def assert_transition(
    from_status: DeviceStatus, to_status: DeviceStatus, device_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS has the edge ``from_status`` to ``to_status``.

    Args:
        from_status: The device's current status.
        to_status: The status a caller wants to move it to.
        device_id: The device's id, when known, for the error message.

    Raises:
        InvalidDeviceTransitionError: No such edge, for instance REVOKED to anything, or PENDING
            straight to LOCKED.
    """
    # The one check every status change goes through (hivemind.entrance.store.protocol.
    # transition_device), so no edge is legal anywhere the table does not list it.
    if not can_transition(from_status, to_status):
        raise InvalidDeviceTransitionError(from_status, to_status, device_id)


def trail_kind(from_status: DeviceStatus, to_status: DeviceStatus) -> str:
    """Return the ``guard.entrance_*`` event kind the edge is recorded as.

    Args:
        from_status: The device's current status.
        to_status: The status it moves to.

    Returns:
        The event kind, e.g. ``"guard.entrance_approved"``.

    Raises:
        InvalidDeviceTransitionError: No such edge.
    """
    assert_transition(from_status, to_status)
    return TRANSITIONS[from_status][to_status]


def is_terminal(status: DeviceStatus) -> bool:
    """Return whether a device in ``status`` never changes status again.

    Args:
        status: The status to check.

    Returns:
        True for DENIED, EXPIRED and REVOKED.
    """
    return status in TERMINAL_STATUSES
