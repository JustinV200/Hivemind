"""Hold pending confirmations: requests a device cannot step up for, until a person confirms them.

A device no person types at cannot step up (ADR-0033), so a request of its that needs step-up
waits as a pending confirmation, pushed to the human and carried out only once a person confirms
it from an interactive device that has just stepped up. ``state`` is its state machine (``PENDING``
settled once: ``CONFIRMED``, ``EXPIRED`` or ``CANCELLED``); ``models`` the records
(``PendingConfirmation``, ``Settlement``, ``HeldAction``, ids); ``flow`` is ``hold``, ``confirm``,
``cancel`` and ``expire_pending``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by the
    routes that guard sensitive actions and the confirmation route (later steps). Persisted by
    ``hivemind.entrance.store.pending``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A held action is handed back at most once, and never for a device no longer approved.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".

Public API:
    - PendingStatus, TRANSITIONS, can_settle, assert_pending_transition, HELD_KIND,
      CONFIRMED_KIND, HOLD_ENDED_KIND, SETTLED_KINDS, settled_trail_kind: the machine and the
      trail kind of every edge (state).
    - PendingConfirmation, Settlement, HeldAction, PendingId, new_pending_id, PENDING_ID_PREFIX,
      MAX_HELD_PAYLOAD_BYTES: the records (models).
    - hold, confirm, cancel, expire_pending, DEFAULT_CONFIRMATION_TTL: the flow (flow).
"""

from hivemind.entrance.auth.confirm.flow import (
    DEFAULT_CONFIRMATION_TTL,
    cancel,
    confirm,
    expire_pending,
    hold,
)
from hivemind.entrance.auth.confirm.models import (
    MAX_HELD_PAYLOAD_BYTES,
    PENDING_ID_PREFIX,
    HeldAction,
    PendingConfirmation,
    PendingId,
    Settlement,
    new_pending_id,
)
from hivemind.entrance.auth.confirm.state import (
    CONFIRMED_KIND,
    HELD_KIND,
    HOLD_ENDED_KIND,
    SETTLED_KINDS,
    TRANSITIONS,
    PendingStatus,
    assert_pending_transition,
    can_settle,
    settled_trail_kind,
)

__all__ = [
    "CONFIRMED_KIND",
    "DEFAULT_CONFIRMATION_TTL",
    "HELD_KIND",
    "HOLD_ENDED_KIND",
    "MAX_HELD_PAYLOAD_BYTES",
    "PENDING_ID_PREFIX",
    "SETTLED_KINDS",
    "TRANSITIONS",
    "HeldAction",
    "PendingConfirmation",
    "PendingId",
    "PendingStatus",
    "Settlement",
    "assert_pending_transition",
    "can_settle",
    "cancel",
    "confirm",
    "expire_pending",
    "hold",
    "new_pending_id",
    "settled_trail_kind",
]
