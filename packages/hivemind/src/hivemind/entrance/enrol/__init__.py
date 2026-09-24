"""Enrol devices at the Hive Entrance: invite, redeem, decide at the Hive Stand, and expire.

Every client of the Hive Entrance (the Hive's one HTTP door) is a device enrolled with its own key
and approved at the Hive Stand (the machine the Queen, the orchestrator, runs on) (codingrules 8.15,
ADR-0033). This package is that whole lifecycle. The data half: ``state`` (the ``DeviceStatus``
machine, one table, each edge carrying its ``guard.entrance_*`` trail kind), ``models`` (the
records) and ``console`` (the operator bootstrap that records the Hive Stand's own loopback-bound
console). The behaviour half (roadmap 10.5d): ``invite`` mints a single-use code shown as text and
QR codes; ``redeem`` lets a device present it with a new Ed25519 key or passkey, becoming a PENDING
request; ``decisions`` approves (binding name, capabilities within the ``device`` ceiling, spend
cap, expiry, interactivity) or denies it; ``standing`` revokes, locks, unlocks and expires;
``grants`` holds the pure ceiling and steward rules. Every flow takes one ``EnrolmentDeps``
(``deps``) and records each state change through ``record``, with its trail event in the same step,
before telling every other device and cutting off a device that left its approval.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Persisted by
    ``hivemind.entrance.store``; called by the ``hive entrance`` commands and the enrolment
    routes (later steps). Calls into ``hivemind.entrance.auth``, ``hivemind.entrance.errors``,
    ``hivemind.guard``, ``hivemind.pheromone``, ``hivemind.common`` and waggle; reaches the
    Entrance tables only through the ``EntranceStore`` protocol.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Invite minting, approval, denial, unlock and revocation are loopback-only decisions; the
      routes that call them enforce that, and nothing here can be reached from the remote
      listener except redemption, locking (after step-up) and the steward route.
    - No invite code, private key, signature or password is stored, logged, raised or recorded.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the decision.
    - .claude/codingrules.md Appendix C, "Enrolled device", for the state machine row.

Public API:
    - DeviceStatus, TRANSITIONS, TERMINAL_STATUSES, ENTRY_TRAIL_KINDS, INVITED_TRAIL_KIND,
      APPROVED_TRAIL_KIND, assert_transition, can_transition, trail_kind, is_terminal: the
      enrolled-device state machine.
    - EnrolledDevice, DeviceDescription, DeviceInvite, OperatorCredential: the Entrance's records.
    - ConsoleDeps, bootstrap_operator, change_operator_password, unlock_console_key,
      CONSOLE_KEY_NAME, CONSOLE_CAPABILITIES, CONSOLE_DEVICE_NAME: the operator and the console.
    - EnrolmentDeps, EnrolmentRecords, EnrolmentRules, EnrolmentCeremony, EnrolmentSeams,
      EntranceIdentity: what every flow is built from.
    - SecurityNotice, SecurityNotifier, DeviceOffboarder, GoalLedger, their Null* no-ops and
      recording fakes (RecordingSecurityNotifier, RecordingDeviceOffboarder, FakeGoalLedger):
      the seams later steps implement.
    - MintedInvite, InviteQr, mint_invite, cancel_invite, new_invite_code,
      canonical_invite_code, invite_code_hash, invite_url, INVITE_PATH: invites.
    - Ed25519Proof, Redemption, RedeemFailure, RedeemStep, passkey_options, redeem_ed25519,
      redeem_passkey, ENROLMENT_CHALLENGE_TTL, REDEEM_FAILED_KIND: redemption.
    - ApprovalRequest, approve, deny: decisions on a pending request.
    - LockReason, Revocation, revoke, lock, unlock, expire_due: an admitted device's standing.
    - approval_grant, device_ceiling, steward_grant: what an approval may grant.
    - OPERATOR_ACTOR: the trail's actor for the operator at the Hive Stand.
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
from hivemind.entrance.enrol.decisions import ApprovalRequest, approve, deny
from hivemind.entrance.enrol.deps import (
    DeviceOffboarder,
    EnrolmentCeremony,
    EnrolmentDeps,
    EnrolmentRecords,
    EnrolmentRules,
    EnrolmentSeams,
    EntranceIdentity,
    FakeGoalLedger,
    GoalLedger,
    NullDeviceOffboarder,
    NullGoalLedger,
    NullSecurityNotifier,
    RecordingDeviceOffboarder,
    RecordingSecurityNotifier,
    SecurityNotice,
    SecurityNotifier,
)
from hivemind.entrance.enrol.grants import approval_grant, device_ceiling, steward_grant
from hivemind.entrance.enrol.invite import (
    INVITE_PATH,
    InviteQr,
    MintedInvite,
    cancel_invite,
    canonical_invite_code,
    invite_code_hash,
    invite_url,
    mint_invite,
    new_invite_code,
)
from hivemind.entrance.enrol.models import (
    DeviceDescription,
    DeviceInvite,
    EnrolledDevice,
    OperatorCredential,
)
from hivemind.entrance.enrol.record import OPERATOR_ACTOR
from hivemind.entrance.enrol.redeem import (
    ENROLMENT_CHALLENGE_TTL,
    REDEEM_FAILED_KIND,
    Ed25519Proof,
    RedeemFailure,
    RedeemStep,
    Redemption,
    passkey_options,
    redeem_ed25519,
    redeem_passkey,
)
from hivemind.entrance.enrol.standing import (
    LockReason,
    Revocation,
    expire_due,
    lock,
    revoke,
    unlock,
)
from hivemind.entrance.enrol.state import (
    APPROVED_TRAIL_KIND,
    ENTRY_TRAIL_KINDS,
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
    "APPROVED_TRAIL_KIND",
    "CONSOLE_CAPABILITIES",
    "CONSOLE_DEVICE_NAME",
    "CONSOLE_KEY_NAME",
    "ENROLMENT_CHALLENGE_TTL",
    "ENTRY_TRAIL_KINDS",
    "INVITED_TRAIL_KIND",
    "INVITE_PATH",
    "OPERATOR_ACTOR",
    "REDEEM_FAILED_KIND",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "ApprovalRequest",
    "ConsoleDeps",
    "DeviceDescription",
    "DeviceInvite",
    "DeviceOffboarder",
    "DeviceStatus",
    "Ed25519Proof",
    "EnrolledDevice",
    "EnrolmentCeremony",
    "EnrolmentDeps",
    "EnrolmentRecords",
    "EnrolmentRules",
    "EnrolmentSeams",
    "EntranceIdentity",
    "FakeGoalLedger",
    "GoalLedger",
    "InviteQr",
    "LockReason",
    "MintedInvite",
    "NullDeviceOffboarder",
    "NullGoalLedger",
    "NullSecurityNotifier",
    "OperatorCredential",
    "RecordingDeviceOffboarder",
    "RecordingSecurityNotifier",
    "RedeemFailure",
    "RedeemStep",
    "Redemption",
    "Revocation",
    "SecurityNotice",
    "SecurityNotifier",
    "approval_grant",
    "approve",
    "assert_transition",
    "bootstrap_operator",
    "can_transition",
    "cancel_invite",
    "canonical_invite_code",
    "change_operator_password",
    "deny",
    "device_ceiling",
    "expire_due",
    "invite_code_hash",
    "invite_url",
    "is_terminal",
    "lock",
    "mint_invite",
    "new_invite_code",
    "passkey_options",
    "redeem_ed25519",
    "redeem_passkey",
    "revoke",
    "steward_grant",
    "trail_kind",
    "unlock",
    "unlock_console_key",
]
