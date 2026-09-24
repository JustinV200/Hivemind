"""Hold the enrolled-device model and its state machine, and bootstrap the Hive Stand console.

Every client of the Hive Entrance (the Hive's one HTTP door) is a device enrolled with its own key
and approved at the Hive Stand (the machine the Queen, the orchestrator, runs on) (codingrules 8.15,
ADR-0033). This package holds the data half of that: ``state`` (the ``DeviceStatus`` machine, one
table, each edge carrying its ``guard.entrance_*`` trail kind), ``models`` (``EnrolledDevice``,
``DeviceDescription``, ``DeviceInvite``, ``OperatorCredential``) and ``console`` (the operator
bootstrap that records the Hive Stand's own loopback-bound console, the password change that
re-wraps its key, and the unlock a console login uses). The invite, redemption, approval and
revocation flows (roadmap 10.5d's behaviour half) build on these.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Persisted by
    ``hivemind.entrance.store``; used by the enrolment routes and ``hive entrance`` commands.
    Calls into ``hivemind.entrance.auth``, ``hivemind.entrance.errors``, ``hivemind.common.
    secrets`` and waggle; reaches the Entrance tables only through the ``EntranceStore`` protocol.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Approval, denial, unlock and revocation happen only on the loopback listener; nothing in
      this package can be reached from the remote one except through routes that enforce that.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the decision.
    - .claude/codingrules.md Appendix C, "Enrolled device", for the state machine row.

Public API:
    - DeviceStatus, TRANSITIONS, TERMINAL_STATUSES, INVITED_TRAIL_KIND, assert_transition,
      can_transition, trail_kind, is_terminal: the enrolled-device state machine.
    - EnrolledDevice, DeviceDescription, DeviceInvite, OperatorCredential: the Entrance's records.
    - ConsoleDeps, bootstrap_operator, change_operator_password, unlock_console_key,
      CONSOLE_KEY_NAME, CONSOLE_CAPABILITIES, CONSOLE_DEVICE_NAME: the operator and the Hive
      Stand console.
"""

from hivemind.entrance.enrol.console import (
    CONSOLE_CAPABILITIES,
    CONSOLE_DEVICE_NAME,
    CONSOLE_KEY_NAME,
    ConsoleDeps,
    bootstrap_operator,
    change_operator_password,
    unlock_console_key,
)
from hivemind.entrance.enrol.models import (
    DeviceDescription,
    DeviceInvite,
    EnrolledDevice,
    OperatorCredential,
)
from hivemind.entrance.enrol.state import (
    INVITED_TRAIL_KIND,
    TERMINAL_STATUSES,
    TRANSITIONS,
    DeviceStatus,
    assert_transition,
    can_transition,
    is_terminal,
    trail_kind,
)

__all__ = [
    "CONSOLE_CAPABILITIES",
    "CONSOLE_DEVICE_NAME",
    "CONSOLE_KEY_NAME",
    "INVITED_TRAIL_KIND",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "ConsoleDeps",
    "DeviceDescription",
    "DeviceInvite",
    "DeviceStatus",
    "EnrolledDevice",
    "OperatorCredential",
    "assert_transition",
    "bootstrap_operator",
    "can_transition",
    "change_operator_password",
    "is_terminal",
    "trail_kind",
    "unlock_console_key",
]
