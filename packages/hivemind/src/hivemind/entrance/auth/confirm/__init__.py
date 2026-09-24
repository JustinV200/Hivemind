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
    - PendingStatus, TRANSITIONS, can_settle, assert_pending_transition: the machine (state).
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
    TRANSITIONS,
    PendingStatus,
    assert_pending_transition,
    can_settle,
)

__all__ = [
    "DEFAULT_CONFIRMATION_TTL",
    "MAX_HELD_PAYLOAD_BYTES",
    "PENDING_ID_PREFIX",
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
]
