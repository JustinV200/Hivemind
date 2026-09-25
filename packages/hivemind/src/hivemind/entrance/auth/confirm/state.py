"""Define PendingStatus and the one transition table a pending confirmation's life follows.

A pending confirmation is how a device no person types at gets something that needs step-up
(ADR-0041): its request is held, pushed to the human, and carried out only once a person confirms
it from an interactive device that has just stepped up. This module is its state machine in the
shape codingrules section 9 requires: one ``Enum`` and one table, each edge commented with who
takes it, tested edge by edge. A confirmation is settled exactly once, from PENDING: confirmed,
expired, or cancelled; nothing moves it again, so a held action can be carried out at most once.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.confirm``. Read by
    the pending table (``hivemind.entrance.store.pending``) inside every settlement, and by the
    confirmation flow. Calls into ``hivemind.entrance.errors`` only.

Key invariants:
    - ``TRANSITIONS`` has exactly one entry per ``PendingStatus``; CONFIRMED, EXPIRED and
      CANCELLED map to no edges.
    - Every edge names its trail kind (``SETTLED_KINDS``), and holding one is ``HELD_KIND``: the
      pending table writes each with its change, in one step. The held action still records its
      own event when the caller carries it out.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
    - hivemind.entrance.auth.confirm.flow for hold, confirm, cancel and the expiry sweep.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.entrance.errors import InvalidPendingTransitionError

HELD_KIND = "guard.entrance_held"  # A request was held (the entry into PENDING).
CONFIRMED_KIND = "guard.entrance_confirmed"  # PENDING to CONFIRMED.
HOLD_ENDED_KIND = "guard.entrance_hold_ended"  # PENDING to EXPIRED or CANCELLED (the payload says).

__all__ = [
    "CONFIRMED_KIND",
    "HELD_KIND",
    "HOLD_ENDED_KIND",
    "SETTLED_KINDS",
    "TRANSITIONS",
    "PendingStatus",
    "assert_pending_transition",
    "can_settle",
    "settled_trail_kind",
]


class PendingStatus(Enum):
    """Every state a pending confirmation can be in; see TRANSITIONS for the moves."""

    PENDING = "PENDING"  # Held: waiting for a person to confirm it after stepping up.
    CONFIRMED = "CONFIRMED"  # Terminal: confirmed once; the caller carries the action out.
    EXPIRED = "EXPIRED"  # Terminal: nobody confirmed it before it expired.
    CANCELLED = "CANCELLED"  # Terminal: a person declined it, or its device lost its approval.


# The single transition table (codingrules section 9): from-status -> the statuses it may move to.
TRANSITIONS: Mapping[PendingStatus, frozenset[PendingStatus]] = {
    PendingStatus.PENDING: frozenset(
        {
            # A person confirmed it from an interactive device inside its step-up window.
            PendingStatus.CONFIRMED,
            # Its expiry passed first; the sweep, or a confirmation that came too late, takes it.
            PendingStatus.EXPIRED,
            # A person declined it, or the device that asked stopped being approved.
            PendingStatus.CANCELLED,
        }
    ),
    PendingStatus.CONFIRMED: frozenset(),  # Terminal: carried out at most once.
    PendingStatus.EXPIRED: frozenset(),  # Terminal: the device asks again if it still wants it.
    PendingStatus.CANCELLED: frozenset(),  # Terminal: likewise.
}


# The trail kind each settlement is recorded as; the two ways of ending unconfirmed share one kind,
# and its payload names which (codingrules Appendix C: every edge is an event).
SETTLED_KINDS: Mapping[PendingStatus, str] = {
    PendingStatus.CONFIRMED: CONFIRMED_KIND,
    PendingStatus.EXPIRED: HOLD_ENDED_KIND,
    PendingStatus.CANCELLED: HOLD_ENDED_KIND,
}


def settled_trail_kind(status: PendingStatus) -> str:
    """Return the trail kind a settlement into ``status`` is recorded as.

    Args:
        status: A terminal status: CONFIRMED, EXPIRED or CANCELLED.

    Returns:
        ``"guard.entrance_confirmed"`` or ``"guard.entrance_hold_ended"``.

    Raises:
        InvalidPendingTransitionError: ``status`` is PENDING, which no settlement moves into.
    """
    kind = SETTLED_KINDS.get(status)
    if kind is None:
        raise InvalidPendingTransitionError(PendingStatus.PENDING, status)
    return kind


def can_settle(from_status: PendingStatus, to_status: PendingStatus) -> bool:
    """Return whether TRANSITIONS has the edge ``from_status`` to ``to_status``.

    Args:
        from_status: The confirmation's current status.
        to_status: The status a caller wants to move it to.

    Returns:
        True when the edge exists.
    """
    return to_status in TRANSITIONS[from_status]


def assert_pending_transition(from_status: PendingStatus, to_status: PendingStatus) -> None:
    """Raise unless TRANSITIONS has the edge ``from_status`` to ``to_status``.

    Args:
        from_status: The confirmation's current status.
        to_status: The status a caller wants to move it to.

    Raises:
        InvalidPendingTransitionError: No such edge, e.g. CONFIRMED to anything.
    """
    if not can_settle(from_status, to_status):
        raise InvalidPendingTransitionError(from_status, to_status)
